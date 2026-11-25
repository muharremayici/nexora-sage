from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from tools.core.analysis_snapshot_lineage import evaluate_snapshot_bound_inputs, receipt_binding, write_lineage_receipt
from tools.core.atlas_integrity import build_atlas_commit
from tools.core.config import CODE_MAPS_DIR
from tools.core.typescript_source_binding import (
    checked_source_errors, semantic_config_root_read_errors, semantic_diagnostic_source_errors,
    semantic_program_declaration_errors, semantic_program_source_errors, semantic_workspace_external_errors,
    semantic_workspace_read_errors,
)
from tools.core.typescript_source_binding import capture_compiler_library_inputs, reconcile_compiler_library_inputs
from tools.engines.merge_simulation_engine import _ts_diagnostics_by_project, simulate_dependency_package


def collect(tmp_path, sources, *args, project_config=None, node_preload=None, collector_cwd=None):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    for name, content in sources.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content, encoding="utf-8", newline="")
    (root / "tsconfig.json").write_text(json.dumps(
        project_config if project_config is not None else {"include": ["*.ts"]}), encoding="utf-8")
    config, output = tmp_path / "runtime.json", tmp_path / "diagnostics.json"
    config.write_text(json.dumps({"workspace_root": str(root), "variations": {"MAIN": "."}}), encoding="utf-8")
    result = subprocess.run(
        ["node", *(["--require", str(node_preload)] if node_preload else []),
         str(CODE_MAPS_DIR / "tools/engines/ts_diagnostics_collector.cjs"),
         "--config", str(config), "--out", str(output), *args],
        cwd=collector_cwd or CODE_MAPS_DIR, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["collector_status"] = "OK"
    atlas = {"MAIN": {"project": {"root": str(root)}, "files": {
        # Normal TypeScript AST metadata is SHA-256; Atlas fallback may be MD5.
        name: {"hash": hashlib.sha256(content.encode("utf-8")).hexdigest()}
        for name, content in sources.items()
    }}}
    return payload, atlas


@pytest.fixture
def checked(tmp_path):
    return collect(tmp_path, {"a.ts": "\ufeffexport const value = ;\r\n"})


def write_receipt(raw_dir, payload, atlas):
    return write_lineage_receipt(
        artifact_id="ts_diagnostics", producer="tools.engines.react_frontier_intelligence",
        artifact_payload=payload, atlas=atlas, atlas_commit=build_atlas_commit(atlas),
        raw_dir=raw_dir,
    )


@pytest.fixture
def installed_compiler(tmp_path):
    """Small owned installation, independent of collector-reported paths."""
    base = tmp_path / "sage"
    config = base / "config"
    config.mkdir(parents=True)
    lineage = json.loads((CODE_MAPS_DIR / "config/analysis_snapshot_lineage_contract.json").read_text())
    (config / "analysis_snapshot_lineage_contract.json").write_text(json.dumps(lineage))
    provenance = json.loads((CODE_MAPS_DIR / "config/third_party_distribution_contract.json").read_text())
    owner = next(row for row in provenance["vendored_dependencies"] if row["id"] == "typescript")
    for key in ("manifest_path", "lockfile_path", "runtime_package_path"):
        (base / owner[key]).parent.mkdir(parents=True, exist_ok=True)
    (base / owner["manifest_path"]).write_text(json.dumps({"dependencies": {"typescript": owner["locked_version"]}}))
    (base / owner["lockfile_path"]).write_bytes(b"owned-lock")
    owner["lockfile_sha256"] = hashlib.sha256(b"owned-lock").hexdigest()
    (config / "third_party_distribution_contract.json").write_text(json.dumps(provenance))
    package = base / owner["runtime_package_path"]
    package.write_text(json.dumps({"name": "typescript", "version": owner["locked_version"], "main": "lib/typescript.js"}))
    library = package.parent / "lib"
    library.mkdir()
    (library / "typescript.js").write_bytes(b"/* installed fixture, never executed */")
    (library / "lib.d.ts").write_bytes(b"\xef\xbb\xbfdeclare const example: string;\r\n")
    return base, library, owner


def test_compiler_inventory_preserves_raw_and_actual_host_text_identity(installed_compiler):
    base, library, _ = installed_compiler
    capture = capture_compiler_library_inputs(base, "current")
    assert capture["status"] == "captured"
    raw = (library / "lib.d.ts").read_bytes()
    entry = capture["files"]["lib.d.ts"]
    assert entry["sha256"] == hashlib.sha256(raw).hexdigest()
    assert entry["host_text_sha256"] == hashlib.sha256(raw[3:]).hexdigest()
    assert capture["loaded_code_attested"] is False
    assert capture["snapshot_bound"] is False
    assert capture_compiler_library_inputs(base, "next")["inventory_sha256"] != capture["inventory_sha256"]


@pytest.mark.parametrize("failure", [
    "lock", "version", "module_missing", "entry_escape", "encoding", "package_shape", "read", "directory_as_library",
    "max_files", "max_directory_entries", "max_file_bytes", "max_module_bytes", "max_total_bytes", "invalid_budget",
])
def test_compiler_inventory_failures_are_unavailable_not_authority(installed_compiler, monkeypatch, failure):
    base, library, owner = installed_compiler
    if failure == "lock":
        (base / owner["lockfile_path"]).write_bytes(b"changed")
    elif failure in {"version", "entry_escape", "package_shape"}:
        package = base / owner["runtime_package_path"]
        data = json.loads(package.read_text())
        if failure == "version":
            data["version"] = "0.0.0"
        elif failure == "entry_escape":
            data["main"] = "../outside.js"
        else:
            data = []
        package.write_text(json.dumps(data))
    elif failure == "module_missing":
        (library / "typescript.js").unlink()
    elif failure == "encoding":
        (library / "lib.d.ts").write_bytes(b"\xff\xfe\x00")
    elif failure == "directory_as_library":
        (library / "lib.d.ts").unlink()
        (library / "lib.d.ts").mkdir()
    elif failure == "read":
        original = Path.open
        def denied(candidate, *args, **kwargs):
            if candidate.name == "lib.d.ts":
                raise PermissionError("injected library read failure")
            return original(candidate, *args, **kwargs)
        monkeypatch.setattr(Path, "open", denied)
    else:
        contract = base / "config/analysis_snapshot_lineage_contract.json"
        data = json.loads(contract.read_text())
        policy = data["artifacts"]["ts_diagnostics"]["compiler_library_inputs"]
        policy["max_files" if failure == "invalid_budget" else failure] = False if failure == "invalid_budget" else 1
        if failure == "max_files":
            (library / "lib.extra.d.ts").write_bytes(b"")
        contract.write_text(json.dumps(data))
    capture = capture_compiler_library_inputs(base, "current")
    assert capture["status"] == "unavailable"
    assert capture["files"] == {}
    assert capture["error"]
    assert "inventory_sha256" not in capture


def test_compiler_inventory_rejects_aliased_library(installed_compiler):
    base, library, _ = installed_compiler
    (library / "lib.d.ts").unlink()
    outside = base / "outside.d.ts"
    outside.write_text("declare const other: number;")
    try:
        (library / "lib.d.ts").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"Host cannot create symlink fixture: {error}")
    assert capture_compiler_library_inputs(base, "current")["error"] == "aliased_compiler_input"


@pytest.mark.parametrize('owned', [False, True, 'context'])
def test_compiler_capsule_same_single_read_inventory_and_no_writes(installed_compiler, monkeypatch, owned):
    from tools.core import typescript_source_binding as binding
    from tools.core.release_validation_source_recipe import observe_compiler_inventory_role
    base, library, owner = installed_compiler
    paths = [base / owner[key] for key in ('manifest_path', 'lockfile_path', 'runtime_package_path')]
    paths += [library / name for name in ('typescript.js', 'lib.d.ts')]
    expected_bytes = {p: p.read_bytes() for p in paths}
    entry = expected_bytes[library / 'lib.d.ts']
    expected = {
        'version': 'v1', 'authority': 'producer_preinvocation_installation_capture',
        'collector_run_id': 'single-read', 'status': 'captured',
        'files': {'lib.d.ts': {'sha256': hashlib.sha256(entry).hexdigest(),
                             'host_text_sha256': hashlib.sha256(entry[3:]).hexdigest()}},
        'scope': 'positive_compiler_library_reads_only', 'loaded_code_attested': False, 'snapshot_bound': False,
        'library_root': library.as_posix(), 'installed_module': (library / 'typescript.js').as_posix(),
        'installed_module_sha256': hashlib.sha256(expected_bytes[library / 'typescript.js']).hexdigest(),
        'compiler_version': owner['locked_version'],
        'manifest_sha256': hashlib.sha256(expected_bytes[paths[0]]).hexdigest(),
        'lockfile_sha256': hashlib.sha256(expected_bytes[paths[1]]).hexdigest(),
        'package_sha256': hashlib.sha256(expected_bytes[paths[2]]).hexdigest(),
    }
    expected['inventory_sha256'] = hashlib.sha256(json.dumps(
        expected, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
    expected_policy = json.loads((base/'config/analysis_snapshot_lineage_contract.json').read_text())['artifacts']['ts_diagnostics']['compiler_library_inputs']
    opened = []
    original = Path.open

    def read_only(candidate, mode='r', *args, **kwargs):
        assert mode in ('r', 'rb'), 'Capture must not persist bytes'
        opened.append(candidate)
        return original(candidate, mode, *args, **kwargs)

    def unexpected_capsule(*args):
        raise AssertionError('Default caller must not allocate a capsule')

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', read_only)
        if owned is not True:
            patch.setattr(binding, '_CompilerLibraryCapsule', unexpected_capsule)
        if owned is True:
            result, capsule = binding._capture_owned_compiler_library_inputs(base, 'single-read')
        elif owned == 'context':
            result, context = binding._capture_compiler_library_inputs_with_context(base, 'single-read')
            capsule = None
        else:
            result, capsule = binding.capture_compiler_library_inputs(base, 'single-read'), None
    assert result == expected
    assert all(opened.count(p) == 1 for p in paths)
    assert len(opened) == len(paths) + 2  # The two canonical policy inputs.
    if owned is True:
        with capsule:
            assert capsule.metadata() == expected
            assert capsule.package_bytes() == expected_bytes[paths[2]]
            assert capsule.library_bytes() == {'lib.d.ts': entry}
    elif owned == 'context':
        assert context['status'] == 'CAPTURED_METADATA_ONLY' and context['capture_policy'] == expected_policy
        assert context['expected_origin'] == {'collector_run_id': 'single-read', 'inventory_sha256': expected['inventory_sha256']}
        assert context['native_reuse_enabled'] is context['origin_authenticated'] is False
        text = json.dumps(context)
        assert not any(token in text for token in [str(base), 'owned-lock', 'installed fixture', 'package_bytes', 'library_bytes'])
        def no_acquisition(*_a, **_k): pytest.fail('Handoff attempted another acquisition')
        with monkeypatch.context() as patch:
            patch.setattr(Path, 'open', no_acquisition); patch.setattr(Path, 'resolve', no_acquisition)
            patch.setattr(subprocess, 'run', no_acquisition); patch.setattr(subprocess, 'Popen', no_acquisition)
            patch.setattr(binding, 'capture_compiler_library_inputs', no_acquisition)
            observed = observe_compiler_inventory_role(result, expected_origin=context['expected_origin'], capture_policy=context['capture_policy'])
        assert observed['status'] == 'OBSERVED_INCOMPLETE_NOT_ADMITTED'
        assert observed['native_reuse_enabled'] is observed['origin_authenticated'] is observed['runtime_equivalence_proven'] is False


def test_compiler_capture_context_is_independent_of_source_view_and_other_invocations(installed_compiler, monkeypatch):
    from tools.core import typescript_source_binding as binding
    base, _, _ = installed_compiler
    policy_path = base/'config/analysis_snapshot_lineage_contract.json'
    shared = json.loads(policy_path.read_text()); held_policy = shared['artifacts']['ts_diagnostics']['compiler_library_inputs']
    original = deepcopy(held_policy); load = binding.load_json_object_strict
    def policy_view(path, *args, **kwargs):
        return shared if path == policy_path else load(path, *args, **kwargs)
    monkeypatch.setattr(binding, 'load_json_object_strict', policy_view)
    first, a = binding._capture_compiler_library_inputs_with_context(base, 'first-context')
    held_policy['rule'] = 'changed source view after the first capture'
    assert a['capture_policy'] == original
    second, b = binding._capture_compiler_library_inputs_with_context(base, 'second-context')
    before = deepcopy((first, second, b, shared))
    a['capture_policy']['rule'] = 'caller mutation'; a['expected_origin']['collector_run_id'] = 'caller mutation'
    assert (first, second, b, shared) == before
    assert first['inventory_sha256'] != second['inventory_sha256']
    assert b['capture_policy']['rule'] == held_policy['rule']


@pytest.mark.parametrize('failure', ['read', 'policy', 'interrupt'])
def test_compiler_capture_context_failure_does_not_publish_success(installed_compiler, monkeypatch, failure):
    from tools.core import typescript_source_binding as binding
    from tools.core.release_validation_source_recipe import observe_compiler_inventory_role
    base, library, _ = installed_compiler
    if failure == 'policy':
        path = base/'config/analysis_snapshot_lineage_contract.json'; value = json.loads(path.read_text())
        value['artifacts']['ts_diagnostics']['compiler_library_inputs']['max_files'] = 0
        path.write_text(json.dumps(value))
    original = Path.open; error = KeyboardInterrupt('private cancellation') if failure == 'interrupt' else OSError('private read failure')
    def read(path, *args, **kwargs):
        if failure != 'policy' and path == library/'lib.d.ts': raise error
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', read)
    monkeypatch.setattr(subprocess, 'run', lambda *_a, **_k: pytest.fail('Capture launched a child'))
    if failure == 'interrupt':
        with pytest.raises(KeyboardInterrupt) as caught:
            binding._capture_compiler_library_inputs_with_context(base, 'failed-context')
        assert caught.value is error
        trace = caught.value.__traceback__
        while trace.tb_frame.f_code.co_name != '_capture_compiler_library_inputs_with_context': trace = trace.tb_next
        context = trace.tb_frame.f_locals['context']
    else:
        result, context = binding._capture_compiler_library_inputs_with_context(base, 'failed-context')
        assert result['status'] == 'unavailable' and result.get('error')
    assert context['status'] == 'UNAVAILABLE' and context['expected_origin'] is context['capture_policy'] is None
    assert context['native_reuse_enabled'] is context['origin_authenticated'] is False
    observed = observe_compiler_inventory_role({}, expected_origin=context['expected_origin'], capture_policy=context['capture_policy'])
    assert observed['status'] == 'BLOCKED' and 'private ' not in json.dumps(context)


@pytest.mark.parametrize('retain_bytes,sink', [(False, []), (True, {})])
def test_compiler_capture_context_refuses_incompatible_sink_before_reads(tmp_path, monkeypatch, retain_bytes, sink):
    from tools.core import typescript_source_binding as binding
    def no_read(*_a, **_k): pytest.fail('Invalid private context reached acquisition')
    monkeypatch.setattr(Path, 'open', no_read); monkeypatch.setattr(Path, 'resolve', no_read)
    with pytest.raises(TypeError, match='compiler_capture_context_requires_metadata_only_sink'):
        binding._capture_compiler_library_inputs(tmp_path, 'invalid', retain_bytes=retain_bytes, capture_context=sink)


@pytest.mark.parametrize('raw', [b'\xef\xbb\xbfdeclare const x: string;\r\n',
    b'\xef\xbb\xbf\xef\xbb\xbfdeclare const x: string;\n',
    'declare const x: "\U0001f680";\r\n'.encode('utf-8')])
def test_compiler_capsule_retains_exact_raw_and_host_hashes(installed_compiler, raw):
    from tools.core.typescript_source_binding import _capture_owned_compiler_library_inputs
    base, library, _ = installed_compiler
    (library / 'lib.d.ts').write_bytes(raw)
    result, capsule = _capture_owned_compiler_library_inputs(base, 'encoding')
    with capsule:
        assert capsule.library_bytes() == {'lib.d.ts': raw}
        assert result['files']['lib.d.ts'] == {'sha256': hashlib.sha256(raw).hexdigest(),
            'host_text_sha256': hashlib.sha256(raw.decode('utf-8-sig').encode('utf-8')).hexdigest()}


def test_compiler_capsule_copies_metadata_and_never_reopens_live_paths(installed_compiler, monkeypatch):
    from tools.core.typescript_source_binding import _capture_owned_compiler_library_inputs
    base, library, owner = installed_compiler
    result, capsule = _capture_owned_compiler_library_inputs(base, 'held-run')
    original = deepcopy(result)
    original_bytes = capsule.library_bytes()
    original_package = capsule.package_bytes()
    (library / 'lib.d.ts').write_bytes(b'changed after capture')
    (base / owner['runtime_package_path']).write_bytes(b'changed package after capture')
    result.update(collector_run_id='child-run', snapshot_bound=True)
    result['files'].clear()
    exported = capsule.metadata()
    exported['files']['lib.d.ts']['sha256'] = '0' * 64
    exported['library_root'] = '/__sage_ts_image__/spoofed'
    capsule.library_bytes().clear()

    def forbidden(*args, **kwargs):
        raise AssertionError('A held capsule cannot reread, resolve or scan paths')

    with monkeypatch.context() as patch, capsule:
        patch.setattr(Path, 'open', forbidden)
        patch.setattr(Path, 'resolve', forbidden)
        patch.setattr(os, 'scandir', forbidden)
        assert capsule.metadata() == original
        assert capsule.library_bytes() == original_bytes
        assert capsule.package_bytes() == original_package
    assert original_bytes['lib.d.ts']  # Disposal cannot revoke a consumer's copy.


@pytest.mark.parametrize('failure', ['late_encoding', 'late_read', 'late_file_budget', 'aggregate', 'count',
    'entries', 'alias', 'lock', 'version', 'module', 'empty', 'run', 'entry'])
def test_compiler_capsule_refuses_without_partial_owned_output(installed_compiler, monkeypatch, failure):
    from tools.core import typescript_source_binding as binding
    base, library, owner = installed_compiler
    # Sorted last, so the late failures happen after a valid declaration read.
    last = library / 'lib.zz.d.ts'
    last.write_bytes(b'declare const later: string;')
    run_id = 'failure'
    if failure == 'late_encoding':
        last.write_bytes(b'\xff')
    elif failure == 'late_read':
        original = Path.open

        def denied(candidate, *args, **kwargs):
            if candidate == last:
                raise PermissionError('synthetic late read denial')
            return original(candidate, *args, **kwargs)

        monkeypatch.setattr(Path, 'open', denied)
    elif failure == 'alias':
        original = Path.resolve

        def alias(candidate, *args, **kwargs):
            return library / 'aliased.d.ts' if candidate == last else original(candidate, *args, **kwargs)

        monkeypatch.setattr(Path, 'resolve', alias)
    elif failure == 'lock':
        (base / owner['lockfile_path']).write_bytes(b'changed lock')
    elif failure in ('version', 'entry'):
        package = base / owner['runtime_package_path']
        data = json.loads(package.read_text())
        data['version' if failure == 'version' else 'main'] = '0.0.0' if failure == 'version' else '../outside.js'
        package.write_text(json.dumps(data))
    elif failure == 'module':
        (library / 'typescript.js').unlink()
    elif failure == 'empty':
        last.unlink()
        (library / 'lib.d.ts').unlink()
    elif failure == 'run':
        run_id = ''
    else:
        contract = base / 'config/analysis_snapshot_lineage_contract.json'
        data = json.loads(contract.read_text())
        policy = data['artifacts']['ts_diagnostics']['compiler_library_inputs']
        if failure == 'late_file_budget':
            limit = policy['max_file_bytes'] = 512
            last.write_bytes(b'x' * (limit + 1))
        else:
            policy[{'aggregate': 'max_total_bytes', 'count': 'max_files', 'entries': 'max_directory_entries'}[failure]] = 1
        contract.write_text(json.dumps(data))
    constructed = []
    original = binding._CompilerLibraryCapsule

    def observed(*args):
        constructed.append(True)
        return original(*args)

    monkeypatch.setattr(binding, '_CompilerLibraryCapsule', observed)
    result, capsule = binding._capture_owned_compiler_library_inputs(base, run_id)
    assert capsule is None and not constructed
    assert result['status'] == 'unavailable' and result['files'] == {} and result['error']
    assert 'inventory_sha256' not in result


@pytest.mark.parametrize('exceptional', [False, True])
def test_compiler_capsule_disposal_and_context_failure_do_not_reopen(installed_compiler, exceptional):
    from tools.core.typescript_source_binding import _capture_owned_compiler_library_inputs
    base, _, _ = installed_compiler
    _, capsule = _capture_owned_compiler_library_inputs(base, 'lifetime')
    failure = RuntimeError('consumer failure')
    if exceptional:
        with pytest.raises(RuntimeError) as caught:
            with capsule:
                raise failure
        assert caught.value is failure
    else:
        with capsule:
            assert capsule.package_bytes()
    capsule.dispose()
    assert repr(capsule) == '<_CompilerLibraryCapsule disposed>'
    for operation in (capsule.metadata, capsule.library_bytes, capsule.package_bytes, capsule.__enter__):
        with pytest.raises(ValueError, match='^compiler_library_capsule_disposed$'):
            operation()
    assert capsule._metadata_json is None and capsule._package is None and capsule._libraries == ()


def test_compiler_capsule_rejects_serialization_copy_and_foreign_construction(installed_compiler):
    import copy
    import pickle
    from tools.core import typescript_source_binding as binding
    base, _, _ = installed_compiler
    _, capsule = binding._capture_owned_compiler_library_inputs(base, 'not-a-wire-object')
    with capsule:
        assert repr(capsule) == '<_CompilerLibraryCapsule live>'
        for operation in (json.dumps, pickle.dumps, copy.copy, copy.deepcopy):
            with pytest.raises(TypeError):
                operation(capsule)
        with pytest.raises(TypeError, match='compiler_library_capsule_owner_required'):
            binding._CompilerLibraryCapsule(object(), {}, b'foreign', {})


def test_compiler_capsule_interruption_propagates_without_publication(installed_compiler, monkeypatch):
    from tools.core import typescript_source_binding as binding
    base, library, _ = installed_compiler
    last = library / 'lib.zz.d.ts'
    last.write_bytes(b'never read')
    original = Path.open
    failure = KeyboardInterrupt('synthetic cancellation')

    def interrupted(candidate, *args, **kwargs):
        if candidate == last:
            raise failure
        return original(candidate, *args, **kwargs)

    def forbidden(*args):
        raise AssertionError('Interrupted capture cannot publish a capsule')

    monkeypatch.setattr(Path, 'open', interrupted)
    monkeypatch.setattr(binding, '_CompilerLibraryCapsule', forbidden)
    with pytest.raises(KeyboardInterrupt) as caught:
        binding._capture_owned_compiler_library_inputs(base, 'cancelled')
    assert caught.value is failure
    trace = caught.value.__traceback__
    while trace.tb_frame.f_code.co_name != '_capture_compiler_library_inputs':
        trace = trace.tb_next
    assert trace.tb_frame.f_locals['retained'] == {}


def test_compiler_capsule_accepts_empty_library_and_exact_existing_budgets(installed_compiler):
    from tools.core.typescript_source_binding import _capture_owned_compiler_library_inputs
    base, library, owner = installed_compiler
    (library / 'lib.empty.d.ts').write_bytes(b'')
    names = ('lib.d.ts', 'lib.empty.d.ts')
    declarations = {name: (library / name).read_bytes() for name in names}
    inputs = [base / owner[key] for key in ('manifest_path', 'lockfile_path', 'runtime_package_path')]
    inputs += [library / 'typescript.js', *(library / name for name in names)]
    sizes = [p.stat().st_size for p in inputs]
    contract = base / 'config/analysis_snapshot_lineage_contract.json'
    data = json.loads(contract.read_text())
    data['artifacts']['ts_diagnostics']['compiler_library_inputs'].update(
        max_files=2, max_directory_entries=3, max_file_bytes=max(sizes),
        max_module_bytes=(library / 'typescript.js').stat().st_size, max_total_bytes=sum(sizes))
    contract.write_text(json.dumps(data))
    result, capsule = _capture_owned_compiler_library_inputs(base, 'exact-boundary')
    assert result['status'] == 'captured'
    with capsule:
        assert capsule.library_bytes() == declarations
        assert set(capsule.metadata()['files']) == set(names)
        assert capsule.metadata()['files']['lib.empty.d.ts']['sha256'] == hashlib.sha256(b'').hexdigest()


def test_compiler_capsule_invocation_identity_stays_distinct(installed_compiler):
    from tools.core.typescript_source_binding import _capture_owned_compiler_library_inputs
    base, _, _ = installed_compiler
    first, a = _capture_owned_compiler_library_inputs(base, 'first')
    second, b = _capture_owned_compiler_library_inputs(base, 'second')
    with a, b:
        assert first['inventory_sha256'] != second['inventory_sha256']
        assert a.metadata()['collector_run_id'] == 'first' and b.metadata()['collector_run_id'] == 'second'
        assert a.library_bytes() == b.library_bytes()
        assert first['snapshot_bound'] is second['snapshot_bound'] is False


@pytest.fixture
def compiler_plan_context(installed_compiler, request):
    from tools.core.typescript_source_binding import _capture_owned_compiler_library_inputs
    base, library, _ = installed_compiler
    if hasattr(request, 'param'):
        (library / 'lib.d.ts').write_bytes(b'x' * request.param)
    expected, capsule = _capture_owned_compiler_library_inputs(base, 'synthetic-plan')
    profiles = json.loads((base / 'config/analysis_snapshot_lineage_contract.json').read_text())['artifacts']['ts_diagnostics']
    schema = json.loads((CODE_MAPS_DIR / profiles['private_compiler_transport_plan']['schema']).read_text())
    body = {'synthetic': 'source/config size example only'}
    frame = (json.dumps(body, sort_keys=True, separators=(',', ':')) + '\n').encode()
    description = {'jsonl_bytes': len(frame), 'sha256': hashlib.sha256(frame).hexdigest(),
        'source_bytes': 7, 'config_bytes': {'root': 3, 'base': 5}}
    yield {'capsule': capsule, 'expected_inventory': expected, 'base_frame': description,
        'profiles': profiles, 'schema': schema, 'base_body': body, 'library': library}
    capsule.dispose()


def compiler_plan_call(context, **changes):
    from tools.core.typescript_source_binding import _plan_owned_compiler_library_transport
    args = {k: context[k] for k in ('expected_inventory', 'base_frame', 'profiles', 'schema')}
    capsule = changes.pop('capsule', context['capsule'])
    args.update(changes)
    result = _plan_owned_compiler_library_transport(capsule, **args)
    from tools.core.artifact_validator import _validate
    errors = []
    _validate(result, context['schema']['$defs']['result'], context['schema'], ['plan'], errors)
    assert not errors
    return result


@pytest.mark.parametrize('include_package', [False, True])
@pytest.mark.parametrize('compiler_plan_context', [0, 1, 2, 3, 4, 101], indirect=True)
def test_compiler_transport_plan_matches_actual_synthetic_bundle(compiler_plan_context, include_package):
    import base64
    from tools.core.owned_text_bridge import canonical
    c = compiler_plan_context
    result = compiler_plan_call(c, include_package=include_package)
    metadata = c['expected_inventory']
    libraries = []
    for name, raw in sorted(c['capsule'].library_bytes().items()):
        entry = metadata['files'][name]
        libraries.append({'name': name, 'input': {'bytes': len(raw), 'raw_sha256': entry['sha256'],
            'host_text_sha256': entry['host_text_sha256'], 'utf8Base64': base64.b64encode(raw).decode('ascii')}})
    package = c['capsule'].package_bytes()
    compiler = {'inventory_sha256': metadata['inventory_sha256'],
        'installed_module_sha256': metadata['installed_module_sha256'], 'collector_run_id': metadata['collector_run_id'],
        'libraries': libraries, 'package': {'bytes': len(package), 'raw_sha256': metadata['package_sha256'],
            'utf8Base64': base64.b64encode(package).decode('ascii')} if include_package else None}
    actual = (canonical({'base': c['base_body'], 'compiler': compiler}) + '\n').encode('utf-8')
    assert result['status'] == 'COMPILER_TRANSPORT_SIZE_ONLY_NOT_ADMITTED'
    assert result['draft_wire_bytes'] == len(actual)
    assert all(value is True for value in result['constraints'].values())
    assert result['compiler_files'] == len(libraries) + include_package
    assert result['whole_image_within_limit'] is result['peak_rss_bytes'] is None
    assert result['source_freshness_verified'] is False
    assert result['base_content_verified'] is result['current_reader_supported'] is result['raw_frame_emitted'] is False
    assert c['capsule'].metadata() == metadata  # The planner does not dispose its caller's owner.


def test_compiler_transport_plan_exact_wire_boundary(compiler_plan_context):
    c = compiler_plan_context
    needed = compiler_plan_call(c)['draft_wire_bytes']
    wire = c['profiles']['private_owned_config_handoff']
    wire['max_request_bytes'] = needed
    at_limit = compiler_plan_call(c)
    wire['max_request_bytes'] = needed - 1
    over = compiler_plan_call(c)
    assert at_limit['constraints']['draft_wire'] is True
    assert over['constraints']['draft_wire'] is False
    assert over['status'] == 'COMPILER_TRANSPORT_BUDGET_EXCEEDED_NOT_ADMITTED'
    assert over['draft_wire_bytes'] == needed


@pytest.fixture
def wire_plan_context(installed_compiler, monkeypatch, request):
    """Owned synthetic bytes plus model parent metadata; never a real compiler/Atlas run."""
    from types import MappingProxyType
    from tools.core import owned_text_bridge as bridge
    from tools.core.owned_config_input import OwnedConfigInputs
    from tools.core.typescript_source_binding import _capture_owned_compiler_library_inputs
    from tools.tests.fixtures import owned_source_parent_join as join

    def no_process(*args, **kwargs):
        raise AssertionError('wire-plan tests must not launch a process')

    monkeypatch.setattr(subprocess, 'run', no_process)
    monkeypatch.setattr(subprocess, 'Popen', no_process)
    base, library, _ = installed_compiler
    profiles = json.loads((base / 'config/analysis_snapshot_lineage_contract.json').read_text())['artifacts']['ts_diagnostics']
    if getattr(request, 'param', None) == 'wire-budget':
        (library / 'lib.d.ts').write_bytes(b'x' * profiles['private_owned_config_handoff']['max_request_bytes'])
    expected_inventory, capsule = _capture_owned_compiler_library_inputs(base, 'synthetic-wire-plan')
    assert capsule is not None
    raw = '\ufeffexport const value = 1; // 😀\r\n'.encode('utf-8')
    sha = lambda value: hashlib.sha256(value).hexdigest()
    source = bridge.OwnedSourceInput(raw, raw.decode(), sha(raw), bridge.canonical(profiles['private_owned_text_bridge']))
    configs = {'tsconfig.json': b'{"extends":"./configs/tsconfig.base.json","files":["entry.ts"]}',
        'configs/tsconfig.base.json': b'{"compilerOptions":{"strict":true}}'}
    inventory = {'files': {name: {'size_bytes': len(value), 'raw_sha256': sha(value),
        'host_text_sha256': sha(value.decode('utf-8-sig').encode())} for name, value in configs.items()}}
    captured = OwnedConfigInputs(MappingProxyType(configs), bridge.canonical(inventory), 'a' * 64, 'b' * 64)
    compiler = {'version': expected_inventory['compiler_version'], 'source_sha256': expected_inventory['installed_module_sha256']}
    # Model parent references are not an assertion of Atlas validation.
    parent = join.ValidatedParent('{}', bridge.canonical({'snapshot_id': 'd' * 64, 'atlas_sha256': 'e' * 64}), 'f' * 64)
    receiver, wire_schema = join.prepare_join_wire_base(profiles, source, captured, compiler, 'plan-source', 'plan-config')
    assert receiver is not None
    source_text, request = source.request('plan-source')
    config_request, binding = join.config_request(captured, parent, profiles, compiler, 'plan-config')
    source_wire = (source_text + '\n').encode()
    config_wire = (bridge.canonical({'request': config_request, 'expected': binding}) + '\n').encode()
    reference = {'request_id': request['requestId'], 'source_bytes': request['source']['bytes'],
        'source_sha256': request['source']['sha256'], 'source_policy_sha256': request['policySha256']}
    parent_ref = {'snapshot_id': 'd' * 64, 'atlas_sha256': 'e' * 64, 'validation_sha256': 'f' * 64,
        'source_sha256': source.source_sha256}
    bridge.observe_owned_wire_source(receiver, source_wire, reference)
    bridge.observe_owned_wire_config(receiver, config_wire, binding, parent_ref)
    receipt = bridge.owned_wire_base_snapshot(receiver)
    assert receipt['status'] == 'OWNED_WIRE_BASE_DERIVED_NOT_ADMITTED'
    expected_wire = {'receipt_sha256': bridge.policy_identity(receipt), 'binding_sha256': bridge.policy_identity(binding),
        'parent_ref_sha256': bridge.policy_identity(parent_ref), 'source_request_id': 'plan-source', 'config_invocation_id': 'plan-config'}
    schemas = {'plan': json.loads((CODE_MAPS_DIR / profiles['private_compiler_transport_plan']['schema']).read_text()), 'wire': wire_schema}
    yield locals()
    bridge.dispose_owned_wire_base(receiver)
    capsule.dispose()


def wire_plan_call(context, **changes):
    from tools.core.typescript_source_binding import _plan_owned_compiler_transport_from_wire_base
    from tools.core.artifact_validator import _validate
    args = {key: context[key] for key in ('expected_inventory', 'receiver', 'receipt', 'expected_wire', 'profiles', 'schemas')}
    capsule = changes.pop('capsule', context['capsule'])
    args.update(changes)
    result = _plan_owned_compiler_transport_from_wire_base(capsule, **args)
    errors = []
    schema = context['schemas']['plan']
    _validate(result, schema['$defs']['wire_adapter'], schema, ['wire_plan'], errors)
    assert not errors
    return result


@pytest.mark.parametrize('include_package', [False, True])
def test_wire_plan_delegates_once_without_io_encoding_or_promotion(wire_plan_context, monkeypatch, include_package):
    import base64
    import builtins
    from tools.core import typescript_source_binding as binding
    c = wire_plan_context
    original = binding._plan_owned_compiler_library_transport
    expected = original(c['capsule'], expected_inventory=c['expected_inventory'], base_frame=c['receipt']['base'],
        profiles=c['profiles'], schema=c['schemas']['plan'], include_package=include_package)
    calls = []

    def delegate(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError('adapter must use held metadata and bytes only')

    monkeypatch.setattr(binding, '_plan_owned_compiler_library_transport', delegate)
    with monkeypatch.context() as m:
        m.setattr(Path, 'open', forbidden)
        m.setattr(Path, 'resolve', forbidden)
        m.setattr(builtins, 'open', forbidden)
        m.setattr(base64, 'b64encode', forbidden)
        result = wire_plan_call(c, include_package=include_package)
    assert len(calls) == 1 and calls[0]['base_frame'] == c['receipt']['base']
    assert result['status'] == 'WIRE_BASE_COMPILER_PLAN_RETURNED_NOT_ADMITTED'
    assert result['plan'] == expected
    assert result['receipt_sha256'] == c['expected_wire']['receipt_sha256']
    assert all(result[k] is False for k in ('base_content_verified', 'send_verified', 'native_execution_attested',
        'production_enabled', 'semantic_lineage', 'cache_eligible'))
    assert expected['whole_image_within_limit'] is expected['peak_rss_bytes'] is None
    assert expected['source_freshness_verified'] is expected['current_reader_supported'] is False
    result['plan']['constraints']['draft_wire'] = False
    assert wire_plan_call(c, include_package=include_package)['plan'] == expected
    assert len(calls) == 2  # A second explicit call, not hidden work in the first call.


@pytest.mark.parametrize('kind', ['foreign', 'uninitialized', 'missing', 'disposed', 'incomplete'])
def test_wire_plan_refuses_noncomplete_owner_before_delegation(wire_plan_context, monkeypatch, kind):
    from tools.core import owned_text_bridge as bridge, typescript_source_binding as binding
    c = wire_plan_context
    if kind == 'foreign': receiver = object()
    elif kind == 'uninitialized': receiver = object.__new__(bridge._OwnedWireBase)
    elif kind == 'missing': receiver = None
    elif kind == 'disposed':
        bridge.dispose_owned_wire_base(c['receiver'])
        receiver = c['receiver']
    else:
        receiver, _ = c['join'].prepare_join_wire_base(c['profiles'], c['source'], c['captured'], c['compiler'], 'plan-source', 'plan-config')
    monkeypatch.setattr(binding, '_plan_owned_compiler_library_transport', lambda *a, **k: pytest.fail('unexpected planner'))
    try:
        result = wire_plan_call(c, receiver=receiver)
        assert result['code'] == 'wire_plan_owner_unavailable' and result['plan'] is None
    finally:
        bridge.dispose_owned_wire_base(receiver)


@pytest.mark.parametrize('field', ['base', 'component', 'flag', 'raw', 'incomplete', 'absent'])
def test_wire_plan_refuses_changed_receipt(wire_plan_context, monkeypatch, field):
    from tools.core import typescript_source_binding as binding
    c = wire_plan_context
    receipt = deepcopy(c['receipt'])
    if field == 'base': receipt['base']['jsonl_bytes'] += 1
    elif field == 'component': receipt['components']['source']['sha256'] = '0' * 64
    elif field == 'flag': receipt['send_verified'] = True
    elif field == 'raw': receipt['raw'] = 'not allowed'
    elif field == 'incomplete': receipt['status'] = 'OWNED_WIRE_BASE_UNAVAILABLE'
    else: receipt = None
    monkeypatch.setattr(binding, '_plan_owned_compiler_library_transport', lambda *a, **k: pytest.fail('unexpected planner'))
    assert wire_plan_call(c, receipt=receipt)['code'] == 'wire_plan_receipt_refused'


@pytest.mark.parametrize('field', ['receipt_sha256', 'binding_sha256', 'parent_ref_sha256', 'source_request_id', 'config_invocation_id'])
def test_wire_plan_requires_independent_expected_references(wire_plan_context, monkeypatch, field):
    from tools.core import typescript_source_binding as binding
    c = wire_plan_context
    expected = deepcopy(c['expected_wire'])
    expected[field] = '0' * 64 if field.endswith('sha256') else 'other-invocation'
    monkeypatch.setattr(binding, '_plan_owned_compiler_library_transport', lambda *a, **k: pytest.fail('unexpected planner'))
    assert wire_plan_call(c, expected_wire=expected)['code'] == 'wire_plan_identity_mismatch'


@pytest.mark.parametrize('field', ['compiler_version', 'installed_module_sha256'])
def test_wire_plan_refuses_compiler_mismatch(wire_plan_context, monkeypatch, field):
    from tools.core import typescript_source_binding as binding
    c = wire_plan_context
    inventory = deepcopy(c['expected_inventory'])
    inventory[field] = 'other' if field == 'compiler_version' else '0' * 64
    monkeypatch.setattr(binding, '_plan_owned_compiler_library_transport', lambda *a, **k: pytest.fail('unexpected planner'))
    assert wire_plan_call(c, expected_inventory=inventory)['code'] == 'wire_plan_compiler_mismatch'


@pytest.mark.parametrize('kind', ['capsule', 'inventory', 'include_package'])
def test_wire_plan_keeps_existing_inventory_refusals(wire_plan_context, kind):
    c = wire_plan_context
    if kind == 'capsule': changes = {'capsule': object()}
    elif kind == 'inventory':
        inventory = deepcopy(c['expected_inventory']); inventory['collector_run_id'] = 'foreign'
        changes = {'expected_inventory': inventory}
    else: changes = {'include_package': 1}
    assert wire_plan_call(c, **changes)['code'] == 'wire_plan_delegate_unavailable'


@pytest.mark.parametrize('kind', ['source', 'config', 'input', 'auxiliary', 'schema', 'schema_set', 'expected_shape', 'budget'])
def test_wire_plan_refuses_policy_schema_or_metadata_drift(wire_plan_context, monkeypatch, kind):
    from tools.core import typescript_source_binding as binding
    c = wire_plan_context
    profiles, schemas, expected = deepcopy(c['profiles']), deepcopy(c['schemas']), deepcopy(c['expected_wire'])
    code = 'wire_plan_identity_mismatch'
    if kind == 'source': profiles['private_owned_text_bridge']['max_source_bytes'] -= 1
    elif kind == 'config': profiles['private_owned_config_extends']['claim_boundary'] += ' changed'
    elif kind == 'input': profiles['private_owned_config_handoff']['max_request_bytes'] -= 1
    elif kind == 'auxiliary': profiles['atlas_auxiliary_inputs']['max_files'] -= 1
    elif kind == 'schema': schemas['plan']['title'] += ' changed'; code = 'wire_plan_policy_unavailable'
    elif kind == 'schema_set': schemas['unexpected'] = {}; code = 'wire_plan_policy_unavailable'
    elif kind == 'expected_shape': expected['raw'] = 'extra'; code = 'wire_plan_policy_unavailable'
    else: profiles['private_input_image_experiment']['limits']['metadata_bytes'] = 1; code = 'wire_plan_metadata_over_budget'
    monkeypatch.setattr(binding, '_plan_owned_compiler_library_transport', lambda *a, **k: pytest.fail('unexpected planner'))
    assert wire_plan_call(c, profiles=profiles, schemas=schemas, expected_wire=expected)['code'] == code


@pytest.mark.parametrize('kind', ['disposed', 'invalidated'])
def test_wire_plan_rechecks_owner_after_single_delegate(wire_plan_context, monkeypatch, kind):
    from tools.core import owned_text_bridge as bridge, typescript_source_binding as binding
    c = wire_plan_context
    original = binding._plan_owned_compiler_library_transport
    calls = []

    def delegate(*args, **kwargs):
        calls.append(True)
        result = original(*args, **kwargs)
        if kind == 'disposed': bridge.dispose_owned_wire_base(c['receiver'])
        else: bridge.observe_owned_wire_source(c['receiver'], c['source_wire'], c['reference'])
        return result

    monkeypatch.setattr(binding, '_plan_owned_compiler_library_transport', delegate)
    assert wire_plan_call(c)['code'] == 'wire_plan_owner_changed'
    assert len(calls) == 1


@pytest.mark.parametrize('kind', ['unavailable', 'raw', 'promoted', 'budget'])
def test_wire_plan_validates_delegate_without_promoting_budget(wire_plan_context, monkeypatch, kind):
    from tools.core import typescript_source_binding as binding
    c = wire_plan_context
    result = binding._plan_owned_compiler_library_transport(c['capsule'], expected_inventory=c['expected_inventory'],
        base_frame=c['receipt']['base'], profiles=c['profiles'], schema=c['schemas']['plan'])
    if kind == 'unavailable': result['status'] = 'COMPILER_TRANSPORT_PLAN_UNAVAILABLE'
    elif kind == 'raw': result['raw'] = 'extra'
    elif kind == 'promoted': result['base_content_verified'] = True
    else:
        # Controlled schema-valid budget result tests adapter forwarding, not a new size formula.
        result['constraints']['draft_wire'] = False
        result['status'] = 'COMPILER_TRANSPORT_BUDGET_EXCEEDED_NOT_ADMITTED'
    monkeypatch.setattr(binding, '_plan_owned_compiler_library_transport', lambda *a, **k: deepcopy(result))
    output = wire_plan_call(c)
    if kind == 'budget':
        assert output['plan'] == result and output['plan']['constraints']['draft_wire'] is False
        assert output['status'] == 'WIRE_BASE_COMPILER_PLAN_RETURNED_NOT_ADMITTED'
    else:
        assert output['code'] == ('wire_plan_delegate_unavailable' if kind == 'unavailable' else 'wire_plan_result_refused')


@pytest.mark.parametrize('error', [MemoryError, KeyboardInterrupt])
def test_wire_plan_does_not_hide_resource_failure_or_cancellation(wire_plan_context, monkeypatch, error):
    from tools.core import typescript_source_binding as binding

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(binding, '_plan_owned_compiler_library_transport', fail)
    with pytest.raises(error): wire_plan_call(wire_plan_context)


@pytest.mark.parametrize('wire_plan_context', ['wire-budget'], indirect=True)
def test_wire_plan_actual_synthetic_budget_is_not_admission(wire_plan_context):
    result = wire_plan_call(wire_plan_context)
    assert result['status'] == 'WIRE_BASE_COMPILER_PLAN_RETURNED_NOT_ADMITTED'
    assert result['plan']['status'] == 'COMPILER_TRANSPORT_BUDGET_EXCEEDED_NOT_ADMITTED'
    assert result['plan']['constraints']['draft_wire'] is False
    assert result['plan']['draft_wire_bytes'] > result['plan']['wire_limit_bytes']
    assert result['base_content_verified'] is result['send_verified'] is False


def test_wire_plan_owner_metadata_is_copied_and_has_no_raw_frames(wire_plan_context):
    from tools.core.owned_text_bridge import owned_wire_base_plan_context
    c = wire_plan_context
    held = owned_wire_base_plan_context(c['receiver'])
    assert set(held) == {'receipt', 'source_request_id', 'config_invocation_id', 'compiler_version',
        'compiler_source_sha256', 'source_policy_sha256', 'config_policy_sha256', 'input_policy_sha256', 'auxiliary_policy_sha256'}
    assert held['receipt'] == c['receipt']
    held['receipt']['base']['jsonl_bytes'] = 0
    held['compiler_version'] = 'changed-copy'
    assert owned_wire_base_plan_context(c['receiver'])['receipt'] == c['receipt']
    assert wire_plan_call(c)['status'] == 'WIRE_BASE_COMPILER_PLAN_RETURNED_NOT_ADMITTED'


def test_compiler_transport_plan_no_base64_or_live_io(compiler_plan_context, monkeypatch):
    import base64
    c = compiler_plan_context

    def forbidden(*args, **kwargs):
        raise AssertionError('Plan cannot encode raw bytes, read, resolve or scan')

    with monkeypatch.context() as patch:
        patch.setattr(base64, 'b64encode', forbidden)
        patch.setattr(Path, 'open', forbidden)
        patch.setattr(Path, 'resolve', forbidden)
        patch.setattr(os, 'scandir', forbidden)
        result = compiler_plan_call(c, include_package=True)
    assert result['draft_wire_bytes'] > 0
    output = json.dumps(result)
    assert 'declare const' not in output and 'utf8Base64' not in output
    assert str(c['library']) not in output and 'files' not in result


def test_compiler_transport_plan_declared_base_and_return_mutation_stay_non_authoritative(compiler_plan_context):
    c = compiler_plan_context
    first = compiler_plan_call(c)
    first['constraints']['draft_wire'] = False
    first['declared_base_sha256'] = 'changed returned container'
    again = compiler_plan_call(c)
    assert again['constraints']['draft_wire'] is True
    assert again['declared_base_sha256'] == c['base_frame']['sha256']
    base = deepcopy(c['base_frame'])
    base['sha256'] = 'f' * 64  # A different declaration is not verified base content.
    other = compiler_plan_call(c, base_frame=base)
    assert other['context_sha256'] != again['context_sha256']
    assert other['base_content_verified'] is other['source_freshness_verified'] is False
    assert other['whole_image_within_limit'] is other['peak_rss_bytes'] is None


@pytest.mark.parametrize('kind', ['none', 'dict', 'object', 'disposed'])
def test_compiler_transport_plan_refuses_foreign_or_disposed_owner(compiler_plan_context, kind):
    c = compiler_plan_context
    if kind == 'disposed':
        c['capsule'].dispose()
        capsule = c['capsule']
    else:
        capsule = {'none': None, 'dict': c['expected_inventory'], 'object': object()}[kind]
    result = compiler_plan_call(c, capsule=capsule)
    assert result['status'] == 'COMPILER_TRANSPORT_PLAN_UNAVAILABLE'
    assert result['code'] == 'compiler_plan_owner_unavailable' and result['draft_wire_bytes'] is None


@pytest.mark.parametrize('field', ['collector_run_id', 'installed_module_sha256', 'inventory_sha256', 'files', 'library_root'])
def test_compiler_transport_plan_refuses_changed_expected_inventory(compiler_plan_context, field):
    c = compiler_plan_context
    expected = deepcopy(c['expected_inventory'])
    expected[field] = {} if field == 'files' else 'changed separately held identity'
    result = compiler_plan_call(c, expected_inventory=expected)
    assert result['code'] == 'compiler_plan_identity_mismatch'
    assert result['context_sha256'] is None


@pytest.mark.parametrize('field', ['library', 'package'])
def test_compiler_transport_plan_refuses_changed_owned_bytes(compiler_plan_context, field):
    c = compiler_plan_context
    if field == 'library':
        name, raw = c['capsule']._libraries[0]
        c['capsule']._libraries = ((name, raw + b'changed'),)
    else:
        c['capsule']._package += b'changed'
    result = compiler_plan_call(c)
    assert result['code'] == 'compiler_plan_owned_bytes_mismatch'
    assert result['draft_wire_bytes'] is None


@pytest.mark.parametrize('value', [True, -1, 1.5, None, '12'])
def test_compiler_transport_plan_refuses_invalid_base_lengths(compiler_plan_context, value):
    c = compiler_plan_context
    base = deepcopy(c['base_frame'])
    base['source_bytes'] = value
    result = compiler_plan_call(c, base_frame=base)
    assert result['status'] == 'COMPILER_TRANSPORT_PLAN_UNAVAILABLE'
    assert result['draft_wire_bytes'] is None


@pytest.mark.parametrize('kind', ['missing_role', 'extra_raw', 'bad_digest', 'wrong_schema', 'enabled', 'bad_budget', 'metadata_budget'])
def test_compiler_transport_plan_refuses_missing_or_invalid_context(compiler_plan_context, kind):
    c = compiler_plan_context
    if kind == 'missing_role':
        del c['base_frame']['config_bytes']['root']
    elif kind == 'extra_raw':
        c['base_frame']['raw'] = 'not accepted'
    elif kind == 'bad_digest':
        c['base_frame']['sha256'] = 'not-a-digest'
    elif kind == 'wrong_schema':
        c['schema']['title'] += 'changed'
    elif kind == 'enabled':
        c['profiles']['private_compiler_transport_plan']['production_enabled'] = True
    elif kind == 'bad_budget':
        c['profiles']['private_owned_config_handoff']['max_request_bytes'] = False
    else:
        c['profiles']['private_input_image_experiment']['limits']['metadata_bytes'] = 1
    result = compiler_plan_call(c)
    assert result['status'] == 'COMPILER_TRANSPORT_PLAN_UNAVAILABLE'
    assert result['draft_wire_bytes'] is None


@pytest.mark.parametrize('budget', ['source', 'config', 'file', 'compiler_bytes', 'package', 'image'])
def test_compiler_transport_plan_separates_budget_domains(compiler_plan_context, budget):
    c = compiler_plan_context
    image = c['profiles']['private_input_image_experiment']['limits']
    if budget == 'source':
        c['profiles']['private_owned_text_bridge']['max_source_bytes'] = 1
        key = 'source_raw'
    elif budget == 'config':
        c['profiles']['private_owned_config_handoff']['max_config_bytes'] = 1
        key = 'config_raw'
    elif budget == 'file':
        image['compiler_file_bytes'] = 1
        key = 'library_file_raw'
    elif budget == 'compiler_bytes':
        image['compiler_bytes'] = 1
        key = 'image_compiler_raw'
    elif budget == 'package':
        c['profiles']['private_owned_compiler_metadata_calibration']['max_metadata_bytes'] = 1
        key = 'package_metadata_raw'
    else:
        image['image_bytes'] = 1
        key = 'image_content_lower_bound'
    result = compiler_plan_call(c, include_package=True)
    assert result['constraints'][key] is False
    assert result['status'] == 'COMPILER_TRANSPORT_BUDGET_EXCEEDED_NOT_ADMITTED'
    assert result['whole_image_within_limit'] is None


def test_compiler_transport_plan_package_consumes_compiler_count(installed_compiler):
    from tools.core.typescript_source_binding import _capture_owned_compiler_library_inputs
    base, library, _ = installed_compiler
    profiles = json.loads((base / 'config/analysis_snapshot_lineage_contract.json').read_text())['artifacts']['ts_diagnostics']
    for n in range(profiles['compiler_library_inputs']['max_files'] - 1):
        (library / f'lib.extra{n:03}.d.ts').write_bytes(b'')
    expected, capsule = _capture_owned_compiler_library_inputs(base, 'count-boundary')
    try:
        c = {'capsule': capsule, 'expected_inventory': expected, 'profiles': profiles,
            'schema': json.loads((CODE_MAPS_DIR / profiles['private_compiler_transport_plan']['schema']).read_text()),
            'base_frame': {'jsonl_bytes': 3, 'sha256': '0' * 64, 'source_bytes': 0, 'config_bytes': {'root': 0, 'base': 0}}}
        without = compiler_plan_call(c)
        with_package = compiler_plan_call(c, include_package=True)
        assert without['compiler_files'] == 256 and without['constraints']['image_compiler_count'] is True
        assert with_package['compiler_files'] == 257 and with_package['constraints']['image_compiler_count'] is False
        assert with_package['status'] == 'COMPILER_TRANSPORT_BUDGET_EXCEEDED_NOT_ADMITTED'
    finally:
        capsule.dispose()


@pytest.fixture(scope="module")
def compiler_input_run(tmp_path_factory):
    temp = tmp_path_factory.mktemp("compiler-input")
    inventory = capture_compiler_library_inputs(CODE_MAPS_DIR, "library-input")
    assert inventory["status"] == "captured", inventory.get("error")
    payload, atlas = collect(temp, {"a.ts": "export const x: string = 42;"}, "--semantic", "true",
                             "--requestId", "library-input", project_config={"files": ["a.ts"]})
    return payload, atlas, inventory


def test_real_library_reads_match_independent_invocation_inventory_without_live_lookups(compiler_input_run, monkeypatch, tmp_path):
    payload, atlas, inventory = compiler_input_run
    def forbidden(*args, **kwargs):
        raise AssertionError("Reconciliation must not reopen or resolve live paths")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", forbidden)
        patch.setattr(Path, "resolve", forbidden)
        binding = reconcile_compiler_library_inputs(payload, inventory)
    assert binding["status"] == "MATCHED_POSITIVE_READS", binding
    assert "lib.d.ts" in binding["projects"]["MAIN"]["matched_files"]
    assert "lib.d.ts" in binding["projects"]["MAIN"]["matched_program_files"]
    assert binding["loaded_code_attested"] is False
    assert binding["snapshot_bound"] is False
    assert binding["inventory"] == inventory
    with_binding = {**payload, "compiler_library_input_binding": binding}
    receipt = write_receipt(tmp_path, with_binding, atlas)
    assert receipt["status"] == "BLOCKED"
    _, usable = evaluate_snapshot_bound_inputs(
        contract_path=CODE_MAPS_DIR / "config/merge_simulation_input_contract.json",
        raw_dir=tmp_path, expected_snapshot_id=receipt["atlas_snapshot_id"],
        payloads={"ts_diagnostics": with_binding},
    )
    assert "ts_diagnostics" not in usable


def test_program_library_sourcefiles_require_independent_installed_text_and_complete_manifest(
    compiler_input_run, tmp_path,
):
    payload, _, inventory = compiler_input_run
    manifest = payload["projects"]["MAIN"]["checked_source_manifest"]
    library_files = manifest["program_outside_workspace_files"]
    assert manifest["program_outside_workspace_files_total"] == len(library_files)
    assert library_files
    assert all(Path(row["file"]).is_relative_to(Path(inventory["library_root"])) for row in library_files)
    assert reconcile_compiler_library_inputs(payload, inventory)["status"] == "MATCHED_POSITIVE_READS"

    stale = deepcopy(payload)
    relative = os.path.relpath(
        library_files[0]["file"], stale["projects"]["MAIN"]["project_root"],
    ).replace("\\", "/")
    row = next(row for row in stale["projects"]["MAIN"]["checked_source_manifest"]["files"]
               if row["file"] == relative)
    row["sha256"] = "0" * 64
    assert reconcile_compiler_library_inputs(stale, inventory)["status"] == "BLOCKED"

    budgeted, _ = collect(
        tmp_path, {"a.ts": "export const x = 1;"}, "--semantic", "true",
        "--requestId", "library-input", "--maxFiles", "1",
        project_config={"files": ["a.ts"]},
    )
    assert budgeted["projects"]["MAIN"]["checked_source_manifest"][
        "program_outside_workspace_files_total"] > 0
    binding = reconcile_compiler_library_inputs(budgeted, inventory)
    assert binding["status"] == "BLOCKED"
    assert "MAIN:compiler_program_inventory_incomplete" in binding["errors"]


@pytest.mark.parametrize("failure", [
    "nonlibrary", "duplicate", "declaration", "missing_row", "count", "version",
])
def test_program_library_manifest_fails_closed_on_unowned_or_malformed_sourcefile(
    compiler_input_run, failure,
):
    payload, _, inventory = deepcopy(compiler_input_run)
    manifest = payload["projects"]["MAIN"]["checked_source_manifest"]
    entry = manifest["program_outside_workspace_files"][0]
    if failure == "nonlibrary":
        original = entry["file"]
        entry["file"] = str(Path(inventory["library_root"]).parent / "foreign.d.ts").replace("\\", "/")
        original_name = os.path.relpath(original, payload["projects"]["MAIN"]["project_root"]).replace("\\", "/")
        replacement = os.path.relpath(
            entry["file"], payload["projects"]["MAIN"]["project_root"],
        ).replace("\\", "/")
        next(row for row in manifest["files"] if row["file"] == original_name)["file"] = replacement
        expected = "MAIN:compiler_program_nonlibrary_input_unbound"
    elif failure == "duplicate":
        manifest["program_outside_workspace_files"].append(deepcopy(entry))
        manifest["program_outside_workspace_files_total"] += 1
        manifest["source_files_total"] += 1
        manifest["omitted_files"] += 1
        expected = "MAIN:compiler_program_path_unbound"
    elif failure == "declaration":
        entry["declaration"] = False
        expected = "MAIN:compiler_program_source_unbound"
    elif failure == "missing_row":
        relative = os.path.relpath(
            entry["file"], payload["projects"]["MAIN"]["project_root"],
        ).replace("\\", "/")
        manifest["files"] = [row for row in manifest["files"] if row["file"] != relative]
        manifest["omitted_files"] += 1
        expected = "MAIN:compiler_program_manifest_unavailable"
    elif failure == "count":
        manifest["program_outside_workspace_files_total"] = -1
        expected = "MAIN:compiler_program_inventory_unavailable"
    else:
        manifest["version"] = "unknown"
        expected = "MAIN:compiler_program_inventory_unavailable"
    binding = reconcile_compiler_library_inputs(payload, inventory)
    assert binding["status"] == "BLOCKED"
    assert any(error == expected or error.startswith(expected + ":") for error in binding["errors"])


@pytest.mark.parametrize("failure", [
    "stale_run", "nonzero", "unknown_inventory", "compiler_path", "compiler_version", "module_hash",
    "omitted", "read_hash", "read_scope", "read_path", "read_failure", "read_shape", "no_reads", "context_shape",
])
def test_library_reconciliation_fails_closed_without_trusting_child_claims(compiler_input_run, failure):
    payload, _, inventory = deepcopy(compiler_input_run)
    context = payload["projects"]["MAIN"]["semantic_context"]
    row = next(item for item in context["observations"] if item["operation"] == "readFile"
               and item["input_scope"] == "compiler_library")
    if failure == "stale_run":
        payload["meta"]["collector_run_id"] = "old"
    elif failure == "nonzero":
        payload["collector_status"] = "NONZERO_EXIT"
    elif failure == "unknown_inventory":
        inventory["status"] = "unavailable"
    elif failure in {"compiler_path", "compiler_version", "module_hash"}:
        key = {"compiler_path": "installed_module", "compiler_version": "version", "module_hash": "installed_module_sha256"}[failure]
        context["compiler"][key] = "not-the-inventory"
    elif failure == "omitted":
        context["observations_complete"] = False
    elif failure == "read_hash":
        row["value"]["text_sha256"] = "0" * 64
    elif failure == "read_scope":
        row["input_scope"] = "workspace"
    elif failure == "read_path":
        row["path"] = str(Path(inventory["library_root"]).parent / "outside.d.ts")
    elif failure == "read_failure":
        row["access_status"] = "unavailable"
    elif failure == "read_shape":
        row["value"] = None
    elif failure == "context_shape":
        payload["projects"]["MAIN"]["semantic_context"] = []
    else:
        context["observations"] = []
    payload["compiler_library_input_binding"] = {"status": "MATCHED_POSITIVE_READS", "snapshot_bound": True}
    binding = reconcile_compiler_library_inputs(payload, inventory)
    assert binding["status"] == "BLOCKED"
    assert binding["errors"]


def test_actual_library_host_override_does_not_match_preinvocation_inventory(tmp_path):
    inventory = capture_compiler_library_inputs(CODE_MAPS_DIR, "changed-read")
    preload = tmp_path / "library-override.cjs"
    preload.write_text("""
const ts = require(require.resolve("typescript", {paths: [require("path").join(process.cwd(), "tools", "engines")]}));
const original = ts.sys.readFile;
ts.sys.readFile = (candidate, ...args) => {
  const text = original(candidate, ...args);
  return String(candidate).endsWith("lib.d.ts") && typeof text === "string"
    ? text + "\\n// different returned input" : text;
};
""")
    payload, _ = collect(tmp_path, {"a.ts": "export const x = 1;"}, "--semantic", "true",
                         "--requestId", "changed-read", node_preload=preload)
    assert reconcile_compiler_library_inputs(payload, inventory)["status"] == "BLOCKED"


@pytest.mark.parametrize("semantic", [False, True])
def test_frontier_owner_captures_before_launch_and_overwrites_child_authority(tmp_path, monkeypatch, compiler_input_run, semantic):
    from tools.engines import react_frontier_intelligence as frontier
    from types import SimpleNamespace
    payload, _, inventory = deepcopy(compiler_input_run)
    payload["compiler_library_input_binding"] = {"status": "SELF_AUTHORIZED", "snapshot_bound": True}
    events = []
    def capture(base, run_id):
        assert base == CODE_MAPS_DIR and run_id == "library-input"
        events.append("capture")
        return inventory
    def launch(command, **kwargs):
        events.append("launch")
        assert command[command.index("--semantic") + 1] == str(semantic).lower()
        assert set(kwargs["env"]) <= {"PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP"}
        return SimpleNamespace(returncode=0, stdout="", stderr=""), 0.01
    monkeypatch.setattr(frontier, "uuid4", lambda: "library-input")
    monkeypatch.setattr(frontier, "capture_compiler_library_inputs", capture)
    monkeypatch.setattr(frontier, "run_observed_subprocess", launch)
    monkeypatch.setattr(frontier, "load_json_file", lambda *args, **kwargs: payload)
    monkeypatch.setattr(frontier, "save_json_atomic", lambda *args: None)
    result = frontier._collect_ts_diagnostics(semantic=semantic)
    assert events == (["capture", "launch"] if semantic else ["launch"])
    if semantic:
        assert result["compiler_library_input_binding"]["status"] == "MATCHED_POSITIVE_READS"
        assert result["compiler_library_input_binding"]["snapshot_bound"] is False
    else:
        assert "compiler_library_input_binding" not in result


def test_real_frontier_semantic_invocation_persists_owner_reconciliation(tmp_path, monkeypatch):
    from tools.engines import react_frontier_intelligence as frontier
    # Build the same target fixture, then exercise the real Python -> Node ->
    # managed artifact path, not just the standalone comparison helper.
    collect(tmp_path, {"a.ts": "export const x: number = 'invalid';"}, project_config={"files": ["a.ts"]})
    monkeypatch.setattr(frontier, "CONFIG_FILE", tmp_path / "runtime.json")
    monkeypatch.setattr(frontier, "TS_DIAGNOSTICS_PATH", tmp_path / "owned.json")
    saved = []
    monkeypatch.setattr(frontier, "save_json_atomic", lambda path, payload: saved.append(deepcopy(payload)))
    result = frontier._collect_ts_diagnostics(semantic=True)
    assert result["collector_status"] == "OK"
    binding = result["compiler_library_input_binding"]
    assert binding["status"] == "MATCHED_POSITIVE_READS", binding
    assert binding["collector_run_id"] == result["meta"]["collector_run_id"]
    assert saved == [result]


def test_real_collector_binds_exact_parsed_content_including_bom_and_crlf(tmp_path, checked):
    payload, atlas = checked
    policy = json.loads((CODE_MAPS_DIR / "config/analysis_snapshot_lineage_contract.json").read_text(
        encoding="utf-8"))
    assert payload["projects"]["MAIN"]["tsconfig"] == policy["artifacts"]["ts_diagnostics"]["root_config_filename"]
    source = payload["projects"]["MAIN"]["checked_source_manifest"]["files"][0]
    assert source["sha256"] == atlas["MAIN"]["files"]["a.ts"]["hash"]
    assert checked_source_errors(payload, atlas) == []
    receipt = write_receipt(tmp_path, payload, atlas)
    assert receipt["status"] == "COMPLETE"
    assert receipt_binding(raw_dir=tmp_path, artifact_id="ts_diagnostics",
                           artifact_payload=payload, expected_snapshot_id=receipt["atlas_snapshot_id"])[0] == "BOUND"
    tampered = deepcopy(payload)
    tampered["projects"]["MAIN"]["diagnostics"] = []
    assert receipt_binding(raw_dir=tmp_path, artifact_id="ts_diagnostics",
                           artifact_payload=tampered, expected_snapshot_id=receipt["atlas_snapshot_id"])[0] == "MISMATCH"


def test_syntax_atlas_digest_contract_fails_closed_without_matching_identity(checked):
    payload, atlas = checked
    missing = deepcopy(payload)
    missing["projects"]["MAIN"]["checked_source_manifest"]["files"][0].pop("sha256")
    assert "typescript:MAIN:source_content_mismatch:a.ts" in checked_source_errors(missing, atlas)
    fallback = deepcopy(atlas)
    fallback["MAIN"]["files"]["a.ts"]["hash"] = payload["projects"]["MAIN"]["checked_source_manifest"]["files"][0]["md5"]
    assert checked_source_errors(payload, fallback) == []
    missing_fallback = deepcopy(payload)
    missing_fallback["projects"]["MAIN"]["checked_source_manifest"]["files"][0].pop("md5")
    assert "typescript:MAIN:source_content_mismatch:a.ts" in checked_source_errors(missing_fallback, fallback)


@pytest.mark.parametrize("case", ["stale", "missing", "root", "escape", "duplicate", "diagnostic",
                                  "truncated", "semantic", "nonzero", "unknown_project"])
def test_unbound_context_never_receives_complete_lineage(tmp_path, checked, case):
    payload, atlas = checked
    data = payload["projects"]["MAIN"]
    manifest = data["checked_source_manifest"]
    if case == "stale":
        atlas["MAIN"]["files"]["a.ts"]["hash"] = "0" * 64
    elif case == "missing":
        data.pop("checked_source_manifest")
    elif case == "root":
        data["project_root"] = str(tmp_path)
    elif case == "escape":
        manifest["files"][0]["file"] = "../a.ts"
    elif case == "duplicate":
        manifest["files"].append(deepcopy(manifest["files"][0]))
    elif case == "diagnostic":
        data["diagnostics"][0]["file"] = "not-checked.ts"
    elif case == "truncated":
        manifest["complete"] = False
    elif case == "semantic":
        data["mode"] = "semantic"
    elif case == "nonzero":
        payload["collector_status"] = "NONZERO_EXIT"
    elif case == "unknown_project":
        payload["meta"]["execution_scope"]["unavailable_requested_projects"] = ["OTHER"]
    receipt = write_receipt(tmp_path, payload, atlas)
    assert receipt["status"] == "BLOCKED"
    assert receipt["errors"]


@pytest.mark.parametrize("bad_meta", [None, "invalid", {"execution_scope": {"analyzed_projects": [{}]}}])
def test_malformed_scope_is_unavailable_not_an_exception(checked, bad_meta):
    payload, atlas = checked
    payload["meta"] = bad_meta
    assert checked_source_errors(payload, atlas)


def test_syntax_root_binding_does_not_requery_live_filesystem(checked, monkeypatch):
    payload, atlas = checked
    root = atlas["MAIN"]["project"]["root"]
    resolve = Path.resolve
    def denied(path, *args, **kwargs):
        if str(path) == root:
            raise AssertionError("snapshot binder must not resolve the live target root")
        return resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", denied)
    assert checked_source_errors(payload, atlas) == []


def test_real_checked_syntax_diagnostic_stays_syntax_scoped(checked):
    from tools.engines.react_frontier_intelligence import (
        _enrich_findings_with_ts_diagnostics, _ts_diagnostic_findings,
    )

    payload, atlas = checked
    assert checked_source_errors(payload, atlas) == []
    data = payload["projects"]["MAIN"]
    assert data["mode"] == "syntax" and data["diagnostics"]
    findings = _ts_diagnostic_findings(payload)
    assert len(findings) == 1
    assert findings[0]["risk"] == "compiler_reported_syntax_failure"
    assert findings[0]["evidence_scope"] == "checked_syntax_files_only"
    unrelated = [{"project": "MAIN", "file": "a.ts", "dimension": "react_security",
                  "score": 5, "evidence": "static suspicion"}]
    assert _enrich_findings_with_ts_diagnostics(unrelated, payload) == (unrelated, 0)


@pytest.mark.parametrize("suffix", ["\\nested\\..", "\\."])
def test_syntax_root_alias_cannot_borrow_atlas_identity(checked, tmp_path, suffix):
    payload, atlas = checked
    root = atlas["MAIN"]["project"]["root"]
    alias = deepcopy(payload)
    alias["projects"]["MAIN"]["project_root"] = root + suffix
    assert "typescript:MAIN:project_root_mismatch" in checked_source_errors(alias, atlas)
    assert write_receipt(tmp_path, alias, atlas)["status"] == "BLOCKED"


@pytest.mark.parametrize("meta", [{"collector_run_id": "previous-run"}, None, "malformed"])
def test_previous_collector_output_cannot_reenter_current_analysis(monkeypatch, tmp_path, meta):
    from tools.engines import react_frontier_intelligence as frontier
    from types import SimpleNamespace

    collector = tmp_path / "collector.cjs"
    collector.write_text("", encoding="utf-8")
    monkeypatch.setattr(frontier, "TS_COLLECTOR", collector)
    monkeypatch.setattr(frontier, "run_observed_subprocess", lambda *a, **k: (
        SimpleNamespace(returncode=0, stdout="", stderr=""), 0.01))
    monkeypatch.setattr(frontier, "load_json_file", lambda *a, **k: {
        "meta": meta, "projects": {"MAIN": {}}})
    assert frontier._collect_ts_diagnostics()["status"] == "STALE_OUTPUT"


@pytest.mark.parametrize("atlas", [None, "invalid", []])
def test_missing_atlas_is_unavailable_not_an_exception(checked, atlas):
    assert checked_source_errors(checked[0], atlas) == ["typescript_atlas_unavailable"]


def test_diagnostic_and_file_budget_report_actual_coverage(tmp_path):
    sources = {"a.ts": "export const a = ;", "b.ts": "export const b = ;"}
    payload, atlas = collect(tmp_path, sources, "--maxDiagnostics", "1")
    data = payload["projects"]["MAIN"]
    assert data["summary"]["root_files_checked"] == 1
    assert len(data["checked_source_manifest"]["files"]) == 1
    assert data["checked_source_manifest"]["complete"] is False
    assert checked_source_errors(payload, atlas)
    payload, atlas = collect(tmp_path, sources, "--maxFiles", "1")
    assert payload["projects"]["MAIN"]["checked_source_manifest"]["complete"] is False
    assert checked_source_errors(payload, atlas)


def test_semantic_collector_keeps_compiler_context_explicitly_unbound(tmp_path):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a: string = 42;"}, "--semantic", "true", "--maxFiles", "2")
    manifest = payload["projects"]["MAIN"]["checked_source_manifest"]
    assert len(manifest["files"]) <= 2
    assert manifest["scope"] == "semantic_context_not_snapshot_bound"
    assert checked_source_errors(payload, atlas)


def test_semantic_diagnostic_source_text_is_bounded_to_atlas_without_semantic_promotion(tmp_path):
    payload, atlas = collect(
        tmp_path, {"a.ts": "export const a: string = 42;"}, "--semantic", "true",
        project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}},
    )
    data = payload["projects"]["MAIN"]
    assert any(row["file"] == "a.ts" for row in data["diagnostics"])
    assert semantic_diagnostic_source_errors(data, atlas["MAIN"]) == []
    fallback = deepcopy(atlas)
    fallback["MAIN"]["files"]["a.ts"]["hash"] = next(
        row for row in data["checked_source_manifest"]["files"] if row["file"] == "a.ts")["md5"]
    assert semantic_diagnostic_source_errors(data, fallback["MAIN"]) == []
    missing_digest = deepcopy(data)
    next(row for row in missing_digest["checked_source_manifest"]["files"] if row["file"] == "a.ts").pop("md5")
    assert semantic_diagnostic_source_errors(missing_digest, fallback["MAIN"]) == [
        "semantic_diagnostic_source_mismatch:a.ts"]
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in checked_source_errors(payload, atlas)

    stale = deepcopy(atlas)
    stale["MAIN"]["files"]["a.ts"]["hash"] = "0" * 64
    assert semantic_diagnostic_source_errors(data, stale["MAIN"]) == [
        "semantic_diagnostic_source_mismatch:a.ts"]
    assert "typescript:MAIN:semantic_diagnostic_source_mismatch:a.ts" in checked_source_errors(payload, stale)

    missing = deepcopy(data)
    missing["checked_source_manifest"]["files"] = [
        row for row in missing["checked_source_manifest"]["files"] if row["file"] != "a.ts"]
    missing["checked_source_manifest"]["omitted_files"] += 1
    assert semantic_diagnostic_source_errors(missing, atlas["MAIN"]) == [
        "semantic_diagnostic_manifest_missing:a.ts"]

    duplicate = deepcopy(data)
    manifest = duplicate["checked_source_manifest"]
    manifest["files"].append(deepcopy(next(row for row in manifest["files"] if row["file"] == "a.ts")))
    manifest["source_files_total"] += 1
    assert semantic_diagnostic_source_errors(duplicate, atlas["MAIN"]) == [
        "semantic_diagnostic_manifest_ambiguous:a.ts"]

    unsafe = deepcopy(data)
    unsafe["diagnostics"][0]["file"] = "../a.ts"
    assert semantic_diagnostic_source_errors(unsafe, atlas["MAIN"]) == [
        "semantic_diagnostic_file_path_unbound"]
    malformed = deepcopy(data)
    malformed["diagnostics"][0]["file"] = ["a.ts"]
    assert semantic_diagnostic_source_errors(malformed, atlas["MAIN"]) == [
        "semantic_checked_manifest_unavailable"]


def test_semantic_diagnostic_text_does_not_assume_bom_normalization(tmp_path):
    payload, atlas = collect(
        tmp_path, {"a.ts": "\ufeffexport const a: string = 42;\r\n"}, "--semantic", "true",
        project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}},
    )
    data = payload["projects"]["MAIN"]
    assert any(row["file"] == "a.ts" for row in data["diagnostics"])
    assert semantic_diagnostic_source_errors(data, atlas["MAIN"]) == [
        "semantic_diagnostic_source_mismatch:a.ts"]
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in checked_source_errors(payload, atlas)


def test_semantic_manifest_budget_prioritizes_emitted_diagnostic_source(tmp_path):
    payload, atlas = collect(
        tmp_path, {"a.ts": "export const a: string = 42;"}, "--semantic", "true", "--maxFiles", "1",
        project_config={"files": ["a.ts"]},
    )
    data = payload["projects"]["MAIN"]
    manifest = data["checked_source_manifest"]
    assert manifest["source_files_total"] > 1  # Installed library files also consume the program.
    assert manifest["omitted_files"] > 0
    assert [row["file"] for row in manifest["files"]] == ["a.ts"]
    assert semantic_diagnostic_source_errors(data, atlas["MAIN"]) == []
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in checked_source_errors(payload, atlas)


def test_semantic_manifest_budget_keeps_selected_root_without_diagnostic(tmp_path):
    payload, atlas = collect(
        tmp_path, {"a.ts": "export const a = 42;"}, "--semantic", "true", "--maxFiles", "1",
        project_config={"files": ["a.ts"]},
    )
    data = payload["projects"]["MAIN"]
    assert not any(row["file"] == "a.ts" for row in data["diagnostics"])
    assert data["checked_source_manifest"]["omitted_files"] > 0
    assert [row["file"] for row in data["checked_source_manifest"]["files"]] == ["a.ts"]
    assert data["checked_source_manifest"]["selected_root_files"] == ["a.ts"]
    assert data["checked_source_manifest"]["files"][0]["sha256"] == atlas["MAIN"]["files"]["a.ts"]["hash"]
    assert semantic_diagnostic_source_errors(data, atlas["MAIN"]) == []
    stale = deepcopy(atlas)
    stale["MAIN"]["files"]["a.ts"]["hash"] = "0" * 64
    assert semantic_diagnostic_source_errors(data, stale["MAIN"]) == [
        "semantic_root_source_mismatch:a.ts"]
    missing = deepcopy(data)
    missing["checked_source_manifest"]["files"] = []
    missing["checked_source_manifest"]["omitted_files"] += 1
    assert semantic_diagnostic_source_errors(missing, atlas["MAIN"]) == [
        "semantic_root_manifest_missing:a.ts"]
    malformed = deepcopy(data)
    malformed["checked_source_manifest"]["selected_root_files"] = ["../a.ts"]
    assert semantic_diagnostic_source_errors(malformed, atlas["MAIN"]) == [
        "semantic_root_file_path_unbound"]
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in checked_source_errors(payload, atlas)


def test_semantic_program_dependency_without_diagnostic_is_compared_to_atlas(tmp_path):
    payload, atlas = collect(
        tmp_path, {
            "a.ts": 'import { value } from "./b"; export const result = value;',
            "b.ts": "export const value = 42;",
        }, "--semantic", "true",
        project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}},
    )
    data = payload["projects"]["MAIN"]
    assert data["checked_source_manifest"]["selected_root_files"] == ["a.ts"]
    assert all(row["file"] != "b.ts" for row in data["diagnostics"])
    assert semantic_diagnostic_source_errors(data, atlas["MAIN"]) == []
    assert semantic_program_source_errors(data, atlas["MAIN"]) == []
    stale = deepcopy(atlas)
    stale["MAIN"]["files"]["b.ts"]["hash"] = "0" * 64
    assert semantic_program_source_errors(data, stale["MAIN"]) == [
        "semantic_program_source_mismatch:b.ts"]
    assert "typescript:MAIN:semantic_program_source_mismatch:b.ts" in checked_source_errors(
        payload, stale)
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in checked_source_errors(
        payload, atlas)
    receipt = write_receipt(tmp_path, payload, stale)
    assert "typescript:MAIN:semantic_program_source_mismatch:b.ts" in receipt["errors"]
    _, usable = evaluate_snapshot_bound_inputs(
        contract_path=CODE_MAPS_DIR / "config/merge_simulation_input_contract.json",
        raw_dir=tmp_path, expected_snapshot_id=build_atlas_commit(stale)["snapshot_id"],
        payloads={"ts_diagnostics": payload},
    )
    assert "ts_diagnostics" not in usable


def test_semantic_program_source_budget_and_missing_authority_remain_unbound(tmp_path):
    payload, atlas = collect(
        tmp_path, {
            "a.ts": 'import { value } from "./b"; export const result = value;',
            "b.ts": "export const value = 42;",
        }, "--semantic", "true", "--maxFiles", "1",
        project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}},
    )
    data = payload["projects"]["MAIN"]
    assert "semantic_program_source_inventory_incomplete" in semantic_program_source_errors(
        data, atlas["MAIN"])
    missing = deepcopy(atlas)
    missing["MAIN"]["files"].pop("a.ts")
    assert "semantic_program_source_unbound:a.ts" in semantic_program_source_errors(
        data, missing["MAIN"])
    malformed = deepcopy(data)
    malformed["checked_source_manifest"].pop("program_project_source_files")
    assert semantic_program_source_errors(malformed, atlas["MAIN"]) == [
        "semantic_program_source_inventory_unavailable"]
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in checked_source_errors(
        payload, atlas)


@pytest.mark.parametrize("declaration_text", [
    "export interface Shape { x: number; }",
    "\ufeffexport interface Shape { x: number; }\r\n",
])
def test_semantic_program_declaration_text_matches_owned_atlas_auxiliary(tmp_path, declaration_text):
    from tools.core.atlas_typescript_inputs import capture_auxiliary_inputs

    sources = {
        "a.ts": 'import type { Shape } from "./types"; export const shape: Shape = { x: 1 };',
        "types.d.ts": declaration_text,
    }
    payload, atlas = collect(
        tmp_path, sources, "--semantic", "true",
        project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}},
    )
    root = tmp_path / "repo"
    atlas["MAIN"]["files"].pop("types.d.ts")
    atlas["MAIN"]["typescript_auxiliary_inputs"] = capture_auxiliary_inputs(
        root, [(str(root / "types.d.ts"), "types.d.ts", "types.d.ts")])
    data = payload["projects"]["MAIN"]
    manifest = data["checked_source_manifest"]
    assert manifest["program_project_declaration_files"] == ["types.d.ts"]
    assert manifest["program_project_declaration_files_total"] == 1
    assert "types.d.ts" not in manifest["selected_root_files"]
    assert all(row["file"] != "types.d.ts" for row in data["diagnostics"])
    assert semantic_program_declaration_errors(data, atlas["MAIN"]) == []

    stale = deepcopy(atlas)
    stale["MAIN"]["typescript_auxiliary_inputs"]["files"]["types.d.ts"]["host_text_sha256"] = "0" * 64
    assert semantic_program_declaration_errors(data, stale["MAIN"]) == [
        "semantic_program_declaration_mismatch:types.d.ts"]
    assert "typescript:MAIN:semantic_program_declaration_mismatch:types.d.ts" in checked_source_errors(
        payload, stale)
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in checked_source_errors(
        payload, atlas)
    receipt = write_receipt(tmp_path, payload, stale)
    assert "typescript:MAIN:semantic_program_declaration_mismatch:types.d.ts" in receipt["errors"]
    _, usable = evaluate_snapshot_bound_inputs(
        contract_path=CODE_MAPS_DIR / "config/merge_simulation_input_contract.json",
        raw_dir=tmp_path, expected_snapshot_id=build_atlas_commit(stale)["snapshot_id"],
        payloads={"ts_diagnostics": payload},
    )
    assert "ts_diagnostics" not in usable

    missing = deepcopy(atlas)
    missing["MAIN"]["typescript_auxiliary_inputs"]["files"].pop("types.d.ts")
    assert semantic_program_declaration_errors(data, missing["MAIN"]) == [
        "semantic_program_declaration_unbound:types.d.ts"]
    malformed = deepcopy(data)
    malformed["checked_source_manifest"].pop("program_project_declaration_files")
    assert semantic_program_declaration_errors(malformed, atlas["MAIN"]) == [
        "semantic_program_declaration_inventory_unavailable"]
    unsafe_path = deepcopy(data)
    unsafe_path["checked_source_manifest"]["program_project_declaration_files"] = ["../types.d.ts"]
    assert semantic_program_declaration_errors(unsafe_path, atlas["MAIN"]) == [
        "semantic_program_declaration_path_unbound"]
    unsafe_root = deepcopy(atlas["MAIN"])
    unsafe_root["project"]["root"] = str(root / ".." / "repo")
    assert semantic_program_declaration_errors(data, unsafe_root) == [
        "semantic_program_declaration_inventory_unavailable"]


def test_semantic_program_declaration_budget_omission_is_explicit(tmp_path):
    payload, atlas = collect(
        tmp_path, {
            "a.ts": 'import type { Shape } from "./types"; export const shape: Shape = { x: 1 };',
            "types.d.ts": "export interface Shape { x: number; }",
        }, "--semantic", "true", "--maxFiles", "1",
        project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}},
    )
    manifest = payload["projects"]["MAIN"]["checked_source_manifest"]
    assert manifest["program_project_declaration_files_total"] == 1
    assert manifest["program_project_declaration_files"] == []
    assert "semantic_program_declaration_inventory_incomplete" in semantic_program_declaration_errors(
        payload["projects"]["MAIN"], atlas["MAIN"])


def test_semantic_cross_project_program_source_requires_unique_atlas_owner(tmp_path):
    workspace = tmp_path / "workspace"
    alpha, beta = workspace / "alpha", workspace / "beta"
    alpha.mkdir(parents=True)
    beta.mkdir()
    alpha_source = 'import { value } from "../beta/b"; export const result = value;'
    beta_source = "export const value = 42;"
    (alpha / "a.ts").write_text(alpha_source, encoding="utf-8")
    (beta / "b.ts").write_text(beta_source, encoding="utf-8")
    config_content = {"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}}
    (alpha / "tsconfig.json").write_text(json.dumps(config_content), encoding="utf-8")
    (beta / "tsconfig.json").write_text(json.dumps({
        **config_content, "files": ["b.ts"],
    }), encoding="utf-8")
    runtime = tmp_path / "runtime.json"
    output = tmp_path / "diagnostics.json"
    runtime.write_text(json.dumps({
        "workspace_root": str(workspace), "variations": {"ALPHA": "alpha", "BETA": "beta"},
    }), encoding="utf-8")
    result = subprocess.run(
        ["node", str(CODE_MAPS_DIR / "tools/engines/ts_diagnostics_collector.cjs"),
         "--config", str(runtime), "--out", str(output), "--semantic", "true", "--maxFiles", "8"],
        cwd=CODE_MAPS_DIR, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["collector_status"] = "OK"
    atlas = {
        "ALPHA": {"project": {"root": str(alpha)}, "files": {
            "a.ts": {"hash": hashlib.sha256(alpha_source.encode()).hexdigest()},
        }},
        "BETA": {"project": {"root": str(beta)}, "files": {
            "b.ts": {"hash": hashlib.sha256(beta_source.encode()).hexdigest()},
        }},
    }
    data = payload["projects"]["ALPHA"]
    manifest = data["checked_source_manifest"]
    assert manifest["program_workspace_external_files"] == [{
        "file": (beta / "b.ts").as_posix(), "declaration": False,
    }]
    assert manifest["program_workspace_external_files_total"] == 1
    assert semantic_workspace_external_errors(data, atlas, "ALPHA") == []

    stale = deepcopy(atlas)
    stale["BETA"]["files"]["b.ts"]["hash"] = "0" * 64
    assert semantic_workspace_external_errors(data, stale, "ALPHA") == [
        "semantic_workspace_external_mismatch:BETA:b.ts"]
    assert "typescript:ALPHA:semantic_workspace_external_mismatch:BETA:b.ts" in checked_source_errors(
        payload, stale)
    receipt = write_receipt(tmp_path, payload, stale)
    assert "typescript:ALPHA:semantic_workspace_external_mismatch:BETA:b.ts" in receipt["errors"]

    missing = deepcopy(atlas)
    missing.pop("BETA")
    assert semantic_workspace_external_errors(data, missing, "ALPHA") == [
        "semantic_workspace_external_owner_unavailable"]
    ambiguous = deepcopy(atlas)
    ambiguous["MIRROR"] = deepcopy(ambiguous["BETA"])
    assert semantic_workspace_external_errors(data, ambiguous, "ALPHA") == [
        "semantic_workspace_external_owner_ambiguous"]
    malformed = deepcopy(data)
    malformed["checked_source_manifest"].pop("program_workspace_external_files")
    assert semantic_workspace_external_errors(malformed, atlas, "ALPHA") == [
        "semantic_workspace_external_inventory_unavailable"]
    limited_output = tmp_path / "limited.json"
    limited_run = subprocess.run(
        ["node", str(CODE_MAPS_DIR / "tools/engines/ts_diagnostics_collector.cjs"),
         "--config", str(runtime), "--out", str(limited_output), "--semantic", "true",
         "--maxFiles", "1"],
        cwd=CODE_MAPS_DIR, capture_output=True, text=True, timeout=30,
    )
    assert limited_run.returncode == 0, limited_run.stderr
    limited = json.loads(limited_output.read_text(encoding="utf-8"))["projects"]["ALPHA"]
    assert limited["checked_source_manifest"]["program_workspace_external_files_total"] == 1
    assert limited["checked_source_manifest"]["program_workspace_external_files"] == []
    assert semantic_workspace_external_errors(limited, atlas, "ALPHA") == [
        "semantic_workspace_external_inventory_incomplete"]


def test_semantic_cross_project_declaration_uses_unique_owned_auxiliary(tmp_path):
    from tools.core.atlas_typescript_inputs import capture_auxiliary_inputs

    workspace = tmp_path / "workspace"
    alpha, beta = workspace / "alpha", workspace / "beta"
    alpha.mkdir(parents=True)
    beta.mkdir()
    alpha_source = 'import { value } from "../beta/b"; export const result = value;'
    beta_declaration = "export declare const value: number;"
    (alpha / "a.ts").write_text(alpha_source, encoding="utf-8")
    (beta / "b.d.ts").write_text(beta_declaration, encoding="utf-8")
    for root, name in ((alpha, "a.ts"), (beta, "b.d.ts")):
        (root / "tsconfig.json").write_text(json.dumps({
            "files": [name], "compilerOptions": {"noLib": True, "types": []},
        }), encoding="utf-8")
    runtime, output = tmp_path / "runtime.json", tmp_path / "diagnostics.json"
    runtime.write_text(json.dumps({
        "workspace_root": str(workspace), "variations": {"ALPHA": "alpha", "BETA": "beta"},
    }), encoding="utf-8")
    result = subprocess.run(
        ["node", str(CODE_MAPS_DIR / "tools/engines/ts_diagnostics_collector.cjs"),
         "--config", str(runtime), "--out", str(output), "--semantic", "true", "--maxFiles", "8"],
        cwd=CODE_MAPS_DIR, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    data = payload["projects"]["ALPHA"]
    assert data["checked_source_manifest"]["program_workspace_external_files"] == [{
        "file": (beta / "b.d.ts").as_posix(), "declaration": True,
    }]
    atlas = {
        "ALPHA": {"project": {"root": str(alpha)}, "files": {
            "a.ts": {"hash": hashlib.sha256(alpha_source.encode()).hexdigest()},
        }},
        "BETA": {"project": {"root": str(beta)}, "files": {},
                 "typescript_auxiliary_inputs": capture_auxiliary_inputs(
                     beta, [(str(beta / "b.d.ts"), "b.d.ts", "b.d.ts")])},
    }
    assert semantic_workspace_external_errors(data, atlas, "ALPHA") == []
    stale = deepcopy(atlas)
    stale["BETA"]["typescript_auxiliary_inputs"]["files"]["b.d.ts"]["host_text_sha256"] = "0" * 64
    assert semantic_workspace_external_errors(data, stale, "ALPHA") == [
        "semantic_workspace_external_mismatch:BETA:b.d.ts"]


def test_semantic_source_host_read_matches_atlas_content_without_live_target_lookup(tmp_path, monkeypatch):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a: string = 42;"},
                             "--semantic", "true", project_config={"files": ["a.ts"]})
    context = payload["projects"]["MAIN"]["semantic_context"]
    source_reads = [row for row in context["observations"]
                    if row.get("operation") == "readFile" and row.get("path", "").endswith("/a.ts")]
    assert source_reads and all(row["access_status"] == "present" for row in source_reads)
    scoped = {"project_root": context["project_root"], "observations_complete": True,
              "observations": source_reads}
    original = Path.resolve
    def no_live_target(candidate, *args, **kwargs):
        if candidate.is_relative_to(tmp_path / "repo"):
            pytest.fail("semantic read consumer consulted live target")
        return original(candidate, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", no_live_target)
    assert semantic_workspace_read_errors(scoped, atlas["MAIN"]) == []
    fallback = deepcopy(atlas)
    fallback["MAIN"]["files"]["a.ts"]["hash"] = source_reads[0]["value"]["text_md5"]
    assert semantic_workspace_read_errors(scoped, fallback["MAIN"]) == []
    without_md5 = deepcopy(scoped)
    without_md5["observations"][0]["value"].pop("text_md5")
    assert semantic_workspace_read_errors(without_md5, fallback["MAIN"]) == [
        "workspace_read_content_mismatch:a.ts"]
    atlas["MAIN"]["files"]["a.ts"]["hash"] = "0" * 64
    assert semantic_workspace_read_errors(scoped, atlas["MAIN"]) == [
        "workspace_read_content_mismatch:a.ts"]


def test_semantic_workspace_reads_match_actual_source_and_owned_config_capture(tmp_path):
    from tools.core.atlas_typescript_inputs import capture_auxiliary_inputs

    payload, atlas = collect(tmp_path, {"a.ts": "export const a = 1;"},
                             "--semantic", "true",
                             project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}})
    root = tmp_path / "repo"
    config = root / "tsconfig.json"
    atlas["MAIN"]["typescript_auxiliary_inputs"] = capture_auxiliary_inputs(
        root, [(str(config), config.name, config.name)])
    context = payload["projects"]["MAIN"]["semantic_context"]
    assert semantic_workspace_read_errors(context, atlas["MAIN"]) == []
    atlas["MAIN"]["typescript_auxiliary_inputs"]["files"]["tsconfig.json"]["host_text_sha256"] = "0" * 64
    assert semantic_workspace_read_errors(context, atlas["MAIN"]) == [
        "workspace_read_content_mismatch:tsconfig.json"]


def test_semantic_reported_config_root_requires_actual_atlas_bound_read(tmp_path):
    from tools.core.atlas_typescript_inputs import capture_auxiliary_inputs

    payload, atlas = collect(
        tmp_path, {"a.ts": "export const a = 1;",
                   "base.json": json.dumps({"compilerOptions": {"strict": True}})},
        "--semantic", "true",
        project_config={"extends": "./base.json", "files": ["a.ts"],
                        "compilerOptions": {"noLib": True, "types": []}},
    )
    root = tmp_path / "repo"
    atlas["MAIN"]["typescript_auxiliary_inputs"] = capture_auxiliary_inputs(
        root, [(str(root / name), name, name) for name in ("tsconfig.json", "base.json")])
    data = payload["projects"]["MAIN"]
    policy = json.loads((CODE_MAPS_DIR / "config/analysis_snapshot_lineage_contract.json").read_text(
        encoding="utf-8"))
    assert data["tsconfig"] == policy["artifacts"]["ts_diagnostics"]["root_config_filename"]
    assert semantic_config_root_read_errors(data, atlas["MAIN"]) == []
    assert semantic_workspace_read_errors(data["semantic_context"], atlas["MAIN"]) == []
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in checked_source_errors(payload, atlas)

    absent = deepcopy(payload)
    absent_rows = absent["projects"]["MAIN"]["semantic_context"]["observations"]
    absent_rows[:] = [row for row in absent_rows if not (
        row.get("stage") == "config" and row.get("operation") == "readFile"
        and row.get("path") == (root / "tsconfig.json").as_posix())]
    assert any(row.get("operation") == "readFile" for row in absent_rows)
    assert semantic_config_root_read_errors(absent["projects"]["MAIN"], atlas["MAIN"]) == [
        "semantic_config_root_read_unbound"]
    assert "typescript:MAIN:semantic_config_root_read_unbound" in checked_source_errors(absent, atlas)

    duplicated = deepcopy(data)
    root_read = next(row for row in duplicated["semantic_context"]["observations"] if (
        row.get("stage") == "config" and row.get("operation") == "readFile"
        and row.get("path") == (root / "tsconfig.json").as_posix()))
    duplicated["semantic_context"]["observations"].append(deepcopy(root_read))
    assert semantic_config_root_read_errors(duplicated, atlas["MAIN"])
    wrong_stage = deepcopy(data)
    root_read = next(row for row in wrong_stage["semantic_context"]["observations"] if (
        row.get("stage") == "config" and row.get("operation") == "readFile"
        and row.get("path") == (root / "tsconfig.json").as_posix()))
    root_read["stage"] = "compiler"
    assert semantic_config_root_read_errors(wrong_stage, atlas["MAIN"])
    wrong_path = deepcopy(data)
    wrong_path["tsconfig"] = "../base.json"
    assert semantic_config_root_read_errors(wrong_path, atlas["MAIN"])
    inherited_as_root = deepcopy(data)
    inherited_as_root["tsconfig"] = "base.json"
    assert any(row.get("stage") == "config" and row.get("operation") == "readFile"
               and row.get("path") == (root / "base.json").as_posix()
               for row in data["semantic_context"]["observations"])
    assert semantic_config_root_read_errors(inherited_as_root, atlas["MAIN"]) == [
        "semantic_config_root_read_unbound"]
    stale = deepcopy(atlas["MAIN"])
    stale["typescript_auxiliary_inputs"]["files"]["tsconfig.json"]["host_text_sha256"] = "0" * 64
    assert semantic_config_root_read_errors(data, stale)
    stale_base = deepcopy(atlas["MAIN"])
    stale_base["typescript_auxiliary_inputs"]["files"]["base.json"]["host_text_sha256"] = "0" * 64
    assert semantic_workspace_read_errors(data["semantic_context"], stale_base) == [
        "workspace_read_content_mismatch:base.json"]
    no_auxiliary = deepcopy(atlas["MAIN"])
    no_auxiliary.pop("typescript_auxiliary_inputs")
    assert semantic_config_root_read_errors(data, no_auxiliary)


def test_semantic_root_config_policy_unavailable_fails_closed(tmp_path, monkeypatch):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a = 1;"}, "--semantic", "true",
                             project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True}})
    monkeypatch.setattr("tools.core.typescript_source_binding.INPUT_POLICY_PATH",
                        tmp_path / "missing-lineage-contract.json")
    assert semantic_config_root_read_errors(payload["projects"]["MAIN"], atlas["MAIN"]) == [
        "semantic_config_root_read_unbound"]


def test_semantic_source_read_does_not_infer_bom_normalization_or_empty_success(tmp_path):
    payload, atlas = collect(tmp_path, {"a.ts": "\ufeffexport const a = 1;"},
                             "--semantic", "true", project_config={"files": ["a.ts"]})
    context = payload["projects"]["MAIN"]["semantic_context"]
    source_read = next(row for row in context["observations"]
                       if row.get("operation") == "readFile" and row.get("path", "").endswith("/a.ts"))
    scoped = {"project_root": context["project_root"], "observations_complete": True,
              "observations": [source_read]}
    assert semantic_workspace_read_errors(scoped, atlas["MAIN"]) == [
        "workspace_read_content_mismatch:a.ts"]
    scoped["observations"] = []
    assert semantic_workspace_read_errors(scoped, atlas["MAIN"]) == [
        "workspace_positive_reads_unavailable"]


@pytest.mark.parametrize("failure", ["missing", "bad_hash", "omitted", "foreign_scope", "denied",
                                           "wrong_path", "missing_digest", "host_failure"])
def test_semantic_source_host_read_never_promotes_unbound_input(tmp_path, failure):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a = 1;"},
                             "--semantic", "true", project_config={"files": ["a.ts"]})
    context = payload["projects"]["MAIN"]["semantic_context"]
    row = next(row for row in context["observations"]
               if row.get("operation") == "readFile" and row.get("path", "").endswith("/a.ts"))
    scoped = {"project_root": context["project_root"], "observations_complete": True,
              "observations": [dict(row)]}
    observed = scoped["observations"][0]
    if failure == "missing":
        atlas["MAIN"]["files"].pop("a.ts")
    elif failure == "bad_hash":
        atlas["MAIN"]["files"]["a.ts"]["hash"] = "bad"
    elif failure == "omitted":
        scoped["observations_complete"] = False
    elif failure == "foreign_scope":
        observed["input_scope"] = "outside"
    elif failure == "denied":
        observed["allowed"] = False
    elif failure == "wrong_path":
        observed["path"] = (tmp_path / "repo" / "../repo/a.ts").as_posix()
    elif failure == "missing_digest":
        observed["value"] = None
    else:
        observed["io_failure"] = {"operation": "readFileSync", "code": "EACCES"}
    assert semantic_workspace_read_errors(scoped, atlas["MAIN"])


def test_semantic_context_records_actual_config_source_and_resolution_reads(tmp_path):
    sources = {
        "base.json": json.dumps({"compilerOptions": {"strict": True}}),
        "a.ts": 'import { value } from "typed-package"; export const a: string = value;',
        "node_modules/typed-package/package.json": json.dumps({"types": "index.d.ts"}),
        "node_modules/typed-package/index.d.ts": "export const value: number;",
    }
    payload, atlas = collect(tmp_path, sources, "--semantic", "true",
                             project_config={"extends": "./base.json", "files": ["a.ts"]})
    data = payload["projects"]["MAIN"]
    context = data["semantic_context"]
    assert context["observations_complete"] is True
    assert context["snapshot_bound"] is False
    assert context["native_project_equivalent"] is False
    assert context["effective_options"]["strict"] is True
    assert len(context["compiler"]["installed_module_sha256"]) == 64
    reads = {row["path"]: row for row in context["observations"] if row["operation"] == "readFile"}
    for name, content in sources.items():
        row = reads[(tmp_path / "repo" / name).as_posix()]
        assert row["value"]["text_sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert any(row["path"].endswith("/tsconfig.json") for row in reads.values())
    assert any(row["path"].endswith("/lib.d.ts") for row in reads.values())
    assert any(row["operation"] == "fileExists" and row["value"] is False for row in context["observations"])
    assert any(row["code"] == "TS2322" for row in data["diagnostics"])
    receipt = write_receipt(tmp_path, payload, atlas)
    assert receipt["status"] == "BLOCKED"
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in receipt["errors"]
    # A full observation inventory must never impersonate source-snapshot authority.
    assert "export const value" not in json.dumps(context)


@pytest.mark.parametrize("change", ["config", "declaration", "package", "missing_resolution"])
def test_semantic_context_identity_changes_without_root_source_change(tmp_path, change):
    sources = {"a.ts": 'import { value } from "typed-package"; export const a: string = value;',
               "base.json": json.dumps({"compilerOptions": {"strict": True}}),
               "node_modules/typed-package/package.json": json.dumps({"types": "index.d.ts"}),
               "node_modules/typed-package/index.d.ts": "export const value: number;"}
    if change == "missing_resolution":
        sources["a.ts"] = 'import { value } from "./missing"; export const a: string = value;'
    config = {"extends": "./base.json", "files": ["a.ts"]}
    first, _ = collect(tmp_path, sources, "--semantic", "true", project_config=config)
    repeated, _ = collect(tmp_path, sources, "--semantic", "true", project_config=config)
    before = first["projects"]["MAIN"]["semantic_context"]
    assert before["context_sha256"] == repeated["projects"]["MAIN"]["semantic_context"]["context_sha256"]
    if change == "config":
        sources["base.json"] = json.dumps({"compilerOptions": {"strict": False}})
    elif change == "declaration":
        sources["node_modules/typed-package/index.d.ts"] = "export const value: string;"
    elif change == "package":
        sources["node_modules/typed-package/package.json"] = json.dumps({"types": "absent.d.ts"})
    else:
        sources["missing.ts"] = 'export const value = "present";'
    after, _ = collect(tmp_path, sources, "--semantic", "true", project_config=config)
    assert before["context_sha256"] != after["projects"]["MAIN"]["semantic_context"]["context_sha256"]


def test_semantic_context_budget_does_not_truncate_diagnostics_or_claim_binding(tmp_path):
    sources = {"a.ts": "export const a: string = 42;"}
    payload, atlas = collect(tmp_path, sources, "--semantic", "true", "--maxContextEntries", "1")
    context = payload["projects"]["MAIN"]["semantic_context"]
    assert len(context["observations"]) == 1
    assert context["omitted_observations"] > 0
    assert context["observations_complete"] is False
    assert sum(context["access_counts"].values()) == context["observed_calls"]
    assert "context_observation_budget_exceeded" in context["binding_blockers"]
    assert any(row["code"] == "TS2322" for row in payload["projects"]["MAIN"]["diagnostics"])
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


def test_semantic_self_declared_binding_cannot_authorize_receipt(tmp_path, checked):
    payload, atlas = checked
    data = payload["projects"]["MAIN"]
    data["mode"] = "semantic"
    data["semantic_context"] = {"observations_complete": True, "snapshot_bound": True,
                                 "native_project_equivalent": True}
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


@pytest.mark.parametrize("launch_directory", ["installation", "target", "foreign"])
def test_semantic_type_discovery_uses_project_not_launch_directory(tmp_path, launch_directory):
    root = tmp_path / "repo"
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    foreign_types = foreign / "node_modules/@types/foreign-env"
    foreign_types.mkdir(parents=True)
    (foreign_types / "index.d.ts").write_text("declare const FOREIGN_VALUE: string;")
    collector_cwd = {"installation": CODE_MAPS_DIR, "target": root, "foreign": foreign}[launch_directory]
    payload, atlas = collect(tmp_path, {
        "a.ts": "export const value: number = PROJECT_VALUE; export const missing = FOREIGN_VALUE;",
        "node_modules/@types/project-env/index.d.ts": "declare const PROJECT_VALUE: number;",
    }, "--semantic", "true", collector_cwd=collector_cwd,
       project_config={"compilerOptions": {"noLib": True}, "files": ["a.ts"]})
    data = payload["projects"]["MAIN"]
    assert not any(row["code"] == "TS2304" and "PROJECT_VALUE" in row["message"]
                   for row in data["diagnostics"]), data["diagnostics"]
    assert any(row["file"] == "node_modules/@types/project-env/index.d.ts"
               for row in data["checked_source_manifest"]["files"])
    assert any(row["code"] == "TS2304" and "FOREIGN_VALUE" in row["message"]
               for row in data["diagnostics"])
    context = data["semantic_context"]
    assert Path(context["current_directory"]) == root
    assert Path(context["process_current_directory"]) == collector_cwd
    assert Path(context["effective_options"]["configFilePath"]) == root / "tsconfig.json"
    assert context["native_project_equivalent"] is False
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize("launch_directory", ["installation", "foreign"])
def test_inherited_glob_roots_do_not_depend_on_launch_directory(tmp_path, semantic, launch_directory):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    preload = tmp_path / "no-chdir.cjs"
    preload.write_text('process.chdir = () => { throw new Error("global cwd mutation forbidden"); };')
    payload, _ = collect(tmp_path, {
        "config/base.json": json.dumps({"include": ["../src/**/*.ts"],
                                        "exclude": ["../src/excluded/**"],
                                        "compilerOptions": {"noLib": True, "types": []}}),
        "src/a.ts": "export const value = 1;",
        "src/nested/b.ts": "export const value = 2;",
        "src/excluded/skip.ts": "export const value = ;",
        "outside/not-selected.ts": "export const value = ;",
    }, "--semantic", str(semantic).lower(),
       project_config={"extends": "./config/base.json"}, node_preload=preload,
       collector_cwd=CODE_MAPS_DIR if launch_directory == "installation" else foreign)
    data = payload["projects"]["MAIN"]
    assert {row["file"] for row in data["checked_source_manifest"]["files"]} == {"src/a.ts", "src/nested/b.ts"}
    assert data["summary"]["root_files_total"] == 2
    assert not any(row["file"].endswith("skip.ts") for row in data["diagnostics"])
    if semantic:
        assert data["semantic_context"]["native_project_equivalent"] is False
        assert data["semantic_context"]["snapshot_bound"] is False
    else:
        assert data["checked_source_manifest"]["complete"] is True


def test_multi_project_type_context_is_isolated_without_process_chdir(tmp_path):
    root = tmp_path / "workspace"
    for project, kind in [("alpha", "number"), ("beta", "string")]:
        directory = root / project
        library = directory / "node_modules/@types/local-env"
        library.mkdir(parents=True)
        (library / "index.d.ts").write_text(f"declare const PROJECT_VALUE: {kind};")
        (directory / "a.ts").write_text(f"export const value: {kind} = PROJECT_VALUE;")
        (directory / "tsconfig.json").write_text(json.dumps({
            "files": ["a.ts"], "compilerOptions": {"noLib": True}}))
    config, output = tmp_path / "runtime.json", tmp_path / "diagnostics.json"
    config.write_text(json.dumps({"workspace_root": str(root), "variations": {"A": "alpha", "B": "beta"}}))
    preload = tmp_path / "no-chdir.cjs"
    preload.write_text('process.chdir = () => { throw new Error("global cwd mutation forbidden"); };')
    result = subprocess.run(["node", "--require", str(preload),
                             str(CODE_MAPS_DIR / "tools/engines/ts_diagnostics_collector.cjs"),
                             "--config", str(config), "--out", str(output), "--semantic", "true"],
                            cwd=CODE_MAPS_DIR, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text())
    for project, relative in [("A", "alpha"), ("B", "beta")]:
        data = payload["projects"][project]
        assert not any(row["code"] in {"TS2304", "TS2322"} for row in data["diagnostics"])
        assert Path(data["semantic_context"]["current_directory"]) == root / relative
        assert Path(data["semantic_context"]["process_current_directory"]) == CODE_MAPS_DIR
        assert {row["file"] for row in data["checked_source_manifest"]["files"]} == {
            "a.ts", "node_modules/@types/local-env/index.d.ts"}
        assert data["semantic_context"]["snapshot_bound"] is False


def test_semantic_profile_differences_and_project_references_are_visible(tmp_path):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a = 1;"}, "--semantic", "true",
        project_config={"files": ["a.ts"], "references": [{"path": "./other"}],
                        "compilerOptions": {"paths": {"@/*": ["src/*"]}, "skipLibCheck": False}})
    context = payload["projects"]["MAIN"]["semantic_context"]
    assert {"paths", "skipLibCheck"} <= set(context["overridden_options"])
    assert context["project_references"] == 1
    assert "project_references_not_loaded" in context["binding_blockers"]
    assert context["native_project_equivalent"] is False
    errors = checked_source_errors(payload, atlas)
    assert "typescript:MAIN:semantic_compiler_profile_overridden" in errors
    assert "typescript:MAIN:semantic_project_references_unloaded" in errors
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in errors
    altered = deepcopy(payload)
    altered["projects"]["MAIN"]["semantic_context"]["project_references_sha256"] = "0" * 64
    altered_errors = checked_source_errors(altered, atlas)
    assert "typescript:MAIN:semantic_project_references_unloaded" in altered_errors
    assert "typescript:MAIN:semantic_project_references_context_mismatch" not in altered_errors


def test_semantic_selected_roots_context_must_match_checked_manifest(tmp_path):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a = 1;", "b.ts": "export const b = 2;"},
        "--semantic", "true",
        project_config={"files": ["b.ts", "a.ts"], "compilerOptions": {"noLib": True, "types": []}})
    assert semantic_diagnostic_source_errors(payload["projects"]["MAIN"], atlas["MAIN"]) == []
    context = payload["projects"]["MAIN"]["semantic_context"]
    assert context["configured_roots"] == context["selected_roots"] == 2
    assert context["configured_roots_sha256"] == context["selected_roots_sha256"]
    reordered = deepcopy(payload)
    reordered["projects"]["MAIN"]["checked_source_manifest"]["selected_root_files"].reverse()
    assert "typescript:MAIN:semantic_selected_roots_context_mismatch" in checked_source_errors(reordered, atlas)
    for field, value in [
        ("selected_roots", 0),
        ("selected_roots_sha256", "0" * 64),
        ("configured_roots", 0),
    ]:
        changed = deepcopy(payload)
        changed["projects"]["MAIN"]["semantic_context"][field] = value
        errors = checked_source_errors(changed, atlas)
        assert "typescript:MAIN:semantic_selected_roots_context_mismatch" in errors
        assert "typescript:MAIN:semantic_context_not_snapshot_bound" in errors

    missing = deepcopy(payload)
    missing["projects"]["MAIN"]["semantic_context"].pop("selected_roots_sha256")
    assert "typescript:MAIN:semantic_selected_roots_context_unavailable" in checked_source_errors(missing, atlas)
    missing_configured = deepcopy(payload)
    missing_configured["projects"]["MAIN"]["semantic_context"].pop("configured_roots_sha256")
    assert "typescript:MAIN:semantic_selected_roots_context_unavailable" in checked_source_errors(
        missing_configured, atlas)
    contradictory = deepcopy(payload)
    contradictory["projects"]["MAIN"]["semantic_context"]["configured_roots_sha256"] = "0" * 64
    assert "typescript:MAIN:semantic_configured_roots_context_mismatch" in checked_source_errors(
        contradictory, atlas)


def test_partial_root_selection_does_not_infer_configured_list_digest(tmp_path):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a = 1;", "b.ts": "export const b = 2;"},
        "--semantic", "true", "--maxFiles", "1",
        project_config={"files": ["a.ts", "b.ts"], "compilerOptions": {"noLib": True, "types": []}})
    context = payload["projects"]["MAIN"]["semantic_context"]
    assert context["configured_roots"] == 2
    assert context["selected_roots"] == 1
    altered = deepcopy(payload)
    altered["projects"]["MAIN"]["semantic_context"]["configured_roots_sha256"] = "0" * 64
    errors = checked_source_errors(altered, atlas)
    assert "typescript:MAIN:semantic_configured_roots_context_mismatch" not in errors
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in errors


def test_semantic_profile_missing_or_malformed_observation_is_not_clean_profile(tmp_path):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a = 1;"}, "--semantic", "true",
                             project_config={"files": ["a.ts"]})
    baseline = checked_source_errors(payload, atlas)
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in baseline
    assert "typescript:MAIN:semantic_compiler_profile_overridden" in baseline
    assert "typescript:MAIN:semantic_compiler_profile_unavailable" not in baseline
    assert "typescript:MAIN:semantic_project_references_unavailable" not in baseline
    assert "typescript:MAIN:semantic_project_references_context_mismatch" not in baseline
    assert payload["projects"]["MAIN"]["semantic_context"]["project_references_sha256"] == hashlib.sha256(
        b"[]").hexdigest()
    for field, expected in [
        ("overridden_options", "semantic_compiler_profile_unavailable"),
        ("requested_options_sha256", "semantic_compiler_profile_unavailable"),
        ("effective_options", "semantic_compiler_profile_unavailable"),
        ("project_references", "semantic_project_references_unavailable"),
        ("project_references_sha256", "semantic_project_references_unavailable"),
    ]:
        changed = deepcopy(payload)
        changed["projects"]["MAIN"]["semantic_context"].pop(field)
        errors = checked_source_errors(changed, atlas)
        assert f"typescript:MAIN:{expected}" in errors
        assert "typescript:MAIN:semantic_context_not_snapshot_bound" in errors
    for field, value, expected in [
        ("overridden_options", ["paths", "paths"], "semantic_compiler_profile_unavailable"),
        ("requested_options_sha256", True, "semantic_compiler_profile_unavailable"),
        ("effective_options", [], "semantic_compiler_profile_unavailable"),
        ("project_references", True, "semantic_project_references_unavailable"),
        ("project_references_sha256", "invalid", "semantic_project_references_unavailable"),
    ]:
        changed = deepcopy(payload)
        changed["projects"]["MAIN"]["semantic_context"][field] = value
        assert f"typescript:MAIN:{expected}" in checked_source_errors(changed, atlas)
    contradictory_empty = deepcopy(payload)
    contradictory_empty["projects"]["MAIN"]["semantic_context"]["project_references_sha256"] = "0" * 64
    assert "typescript:MAIN:semantic_project_references_context_mismatch" in checked_source_errors(
        contradictory_empty, atlas)


def test_native_alias_resolution_cannot_be_scored_from_bounded_semantic_profile(tmp_path):
    payload, atlas = collect(
        tmp_path,
        {"a.ts": 'import { value } from "@alias/value"; export const result: number = value;',
         "src/value.ts": "export const value = 1;"},
        "--semantic", "true",
        project_config={"files": ["a.ts"], "compilerOptions": {
            "baseUrl": ".", "paths": {"@alias/*": ["src/*"]}, "types": []}},
    )
    root = tmp_path / "repo"
    native = subprocess.run(
        ["node", str(CODE_MAPS_DIR / "tools/engines/node_modules/typescript/bin/tsc"),
         "--noEmit", "--project", str(root / "tsconfig.json")],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert native.returncode == 0, native.stdout + native.stderr
    semantic = payload["projects"]["MAIN"]
    assert any(row["code"] == "TS2307" for row in semantic["diagnostics"])
    assert {"baseUrl", "paths"} <= set(semantic["semantic_context"]["overridden_options"])
    assert "typescript:MAIN:semantic_compiler_profile_overridden" in checked_source_errors(payload, atlas)
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


def test_invalid_semantic_context_budget_fails_before_output(tmp_path):
    output = tmp_path / "absent.json"
    result = subprocess.run(
        ["node", str(CODE_MAPS_DIR / "tools/engines/ts_diagnostics_collector.cjs"),
         "--out", str(output), "--semantic", "true", "--maxContextEntries", "-1"],
        cwd=CODE_MAPS_DIR, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "semantic context observation budget" in result.stderr
    assert not output.exists()


def test_semantic_config_errors_are_not_dropped_from_diagnostics(tmp_path):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a = 1;"}, "--semantic", "true",
        project_config={"files": ["a.ts"], "compilerOptions": {"target": "invalid-target"}})
    assert any(row["code"] == "TS6046" for row in payload["projects"]["MAIN"]["diagnostics"])
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


def test_missing_module_and_denied_external_path_are_distinct_observations(tmp_path):
    (tmp_path / "outside.ts").write_text("export const x = 1;")
    payload, atlas = collect(tmp_path, {"a.ts": 'import "./missing"; import "../outside"; export {};'},
                             "--semantic", "true", project_config={"files": ["a.ts"]})
    context = payload["projects"]["MAIN"]["semantic_context"]
    probes = [row for row in context["observations"] if row["operation"] == "fileExists"]
    missing = next(row for row in probes if row["path"] == (tmp_path / "repo/missing.ts").as_posix())
    denied = next(row for row in context["observations"]
                  if row["operation"] == "directoryExists" and row["path"] == tmp_path.as_posix())
    assert missing["allowed"] is True
    assert missing["access_status"] == "missing"
    assert missing["input_scope"] == "workspace"
    assert missing["value"] is False
    assert denied["allowed"] is False
    assert denied["access_status"] == "outside_boundary"
    assert denied["value"] is False
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


def test_library_reads_are_toolchain_observations_not_project_inputs(tmp_path):
    payload, atlas = collect(tmp_path, {"a.ts": "export const x = 1;"}, "--semantic", "true")
    context = payload["projects"]["MAIN"]["semantic_context"]
    library = next(row for row in context["observations"]
                   if row["operation"] == "readFile" and row["path"].endswith("/lib.d.ts"))
    assert library["input_scope"] == "compiler_library"
    assert library["access_status"] == "present"
    assert Path(library["path"]).is_relative_to(Path(context["compiler"]["library_root"]))
    assert context["compiler"]["authority"] == "collector_toolchain_observation"
    assert context["snapshot_bound"] is False
    receipt = write_receipt(tmp_path, payload, atlas)
    assert receipt["status"] == "BLOCKED"
    assert "typescript:MAIN:compiler_library_not_snapshot_bound" in receipt["errors"]
    assert "typescript:MAIN:auxiliary_input_outside_project" not in receipt["errors"]


@pytest.mark.parametrize("failure", ["realpath", "read"])
def test_host_io_failure_is_unavailable_not_missing(tmp_path, failure):
    preload = tmp_path / "failure.cjs"
    if failure == "realpath":
        preload.write_text("""
const fs = require("fs");
const original = fs.realpathSync.native;
fs.realpathSync.native = (candidate, ...args) => {
  if (String(candidate).endsWith("blocked.ts")) {
    const error = new Error("fixture permission denied"); error.code = "EACCES"; throw error;
  }
  return original(candidate, ...args);
};
""")
    else:
        preload.write_text("""
const ts = require(require.resolve("typescript", {paths: [require("path").join(process.cwd(), "tools", "engines")]}));
const original = ts.sys.readFile;
ts.sys.readFile = (candidate, ...args) => String(candidate).endsWith("blocked.ts")
  ? undefined : original(candidate, ...args);
""")
    payload, atlas = collect(tmp_path, {
        "a.ts": 'import { x } from "./blocked"; export const y = x;',
        "blocked.ts": "export const x = 1;",
    }, "--semantic", "true", project_config={"files": ["a.ts"]}, node_preload=preload)
    context = payload["projects"]["MAIN"]["semantic_context"]
    rows = [row for row in context["observations"] if row["path"].endswith("/blocked.ts")]
    assert any(row["access_status"] == "unavailable" for row in rows)
    assert not any(row["access_status"] == "missing" for row in rows)
    assert "host_access_unavailable" in context["binding_blockers"]
    assert "typescript:MAIN:semantic_host_access_unavailable" in write_receipt(tmp_path, payload, atlas)["errors"]


@pytest.mark.parametrize("operation,fs_operation,error_code", [
    (operation, fs_operation, error_code)
    for operation, fs_operation in [
        ("fileExists", "statSync"), ("directoryExists", "statSync"),
        ("readFile", "readFileSync"), ("getDirectories", "readdirSync"),
        ("readDirectory", "readdirSync"), ("realpath", "realpathSync.native"),
    ]
    for error_code in [None, "EACCES", "EIO", "ENOENT", "UNKNOWN", "STAT_MISSING"]
    if error_code != "STAT_MISSING" or fs_operation == "statSync"
])
def test_swallowed_host_failures_preserve_actual_result_and_error(tmp_path, operation, fs_operation, error_code):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.ts").write_text("export const value = 1;")
    (root / "sub").mkdir()
    candidate = root / "a.ts" if operation in {"fileExists", "readFile"} else root
    collector = CODE_MAPS_DIR / "tools/engines/ts_diagnostics_collector.cjs"
    # Exercise private adapters with the installed TS host. Strip only the CLI
    # invocation; no fake TypeScript filesystem semantics or extra production API.
    script = r"""
const fs = require("fs"), path = require("path"), vm = require("vm");
const {createRequire} = require("module");
const [collector, root, candidate, operation, fsOperation, errorCode] = process.argv.slice(1);
const source = fs.readFileSync(collector, "utf8").replace(/\r\n/g, "\n");
const boundary = source.lastIndexOf("\ntry {\n  main();");
if (boundary < 0) throw new Error("collector entrypoint changed");
let armed = false, calls = 0;
const owner = fsOperation === "realpathSync.native" ? fs.realpathSync : fs;
const key = fsOperation === "realpathSync.native" ? "native" : fsOperation;
const original = owner[key];
const injected = function(...args) {
  if (armed) {
    calls += 1;
    if (errorCode === "STAT_MISSING") return undefined;
    if (errorCode !== "null") {
      const error = new Error("fixture I/O failure, not a diagnostic message");
      if (errorCode !== "UNKNOWN") error.code = errorCode;
      throw error;
    }
  }
  return original.apply(this, args);
};
owner[key] = injected;
const context = {require: createRequire(collector), console, process,
  root, candidate, operation, owner, key, injected, setArmed: value => {armed = value;},
  getCalls: () => calls, __dirname: path.dirname(collector)};
vm.runInNewContext(source.slice(0, boundary) + `
fileSystemObservation = observeFileSystemFailures();
ts = require("typescript");
const recorder = semanticContextRecorder(20);
const raw = {...ts.sys};
const originalHost = raw[operation];
raw[operation] = (...args) => {
  setArmed(true);
  try { return originalHost(...args); } finally { setArmed(false); }
};
const host = observeHost(raw, "config", recorder,
  boundedPathAccess([{root, scope: "workspace"}]), root);
const args = operation === "readDirectory" ? [[".ts"], undefined, ["**/*"]] : [];
let value;
let scopeRestored;
try {
  value = host[operation](candidate, ...args);
  try { fileSystemObservation.run(() => { throw new Error("scope exit fixture"); }); } catch {}
  const afterThrow = fileSystemObservation.run(() => true);
  scopeRestored = afterThrow.count === 0 && afterThrow.failure === null;
}
finally { fileSystemObservation.restore(); }
const observedCalls = getCalls();
const restored = owner[key] === injected;
console.log(JSON.stringify({value: value === undefined ? null : value, scopeRestored,
  context: recorder.finish({binding_blockers: []}), observedCalls, restored}));
`, context);
"""
    result = subprocess.run(["node", "-e", script, str(collector), str(root), str(candidate),
                             operation, fs_operation, error_code or "null"],
                            cwd=CODE_MAPS_DIR, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["restored"] is True
    assert output["scopeRestored"] is True
    assert output["observedCalls"] >= 1
    context = output["context"]
    row, = context["observations"]
    assert row["allowed"] is True  # boundary accepted; the later host call failed
    if error_code is None:
        assert row["access_status"] == "present"
        assert row["error_code"] is None
        assert "io_failure" not in row
        assert "host_access_unavailable" not in context["binding_blockers"]
    else:
        assert row["access_status"] == "unavailable"
        code = {"UNKNOWN": "HOST_IO_UNKNOWN", "STAT_MISSING": "ENOENT"}.get(error_code, error_code)
        assert row["error_code"] == code
        assert row["io_failure"]["code"] == code
        assert row["io_failure"]["count"] == output["observedCalls"]
        assert "host_access_unavailable" in context["binding_blockers"]
        # Retain TS's actual fallback. An empty result cannot conceal the error.
        expected = None if operation == "readFile" else str(candidate)
        if operation in {"fileExists", "directoryExists"}:
            expected = False
        elif operation in {"getDirectories", "readDirectory"}:
            expected = []
        assert output["value"] == expected


def test_in_boundary_names_starting_with_dots_are_not_parent_escapes(tmp_path):
    payload, _ = collect(tmp_path, {
        "a.ts": 'import { x } from "./..local"; export const y: string = x;',
        "..local.ts": "export const x = 1;",
    }, "--semantic", "true", project_config={"files": ["a.ts"]})
    context = payload["projects"]["MAIN"]["semantic_context"]
    assert any(row["path"].endswith("/..local.ts") and row["operation"] == "readFile"
               and row["allowed"] is True for row in context["observations"])
    assert any(row["code"] == "TS2322" for row in payload["projects"]["MAIN"]["diagnostics"])


def test_library_identity_hashes_the_text_actually_read_without_reopening(tmp_path):
    payload, _ = collect(tmp_path, {"a.ts": "export const x = 1;"}, "--semantic", "true")
    before = payload["projects"]["MAIN"]["semantic_context"]
    preload = tmp_path / "library.cjs"
    preload.write_text("""
const ts = require(require.resolve("typescript", {paths: [require("path").join(process.cwd(), "tools", "engines")]}));
const original = ts.sys.readFile;
ts.sys.readFile = (candidate, ...args) => {
  const text = original(candidate, ...args);
  return String(candidate).endsWith("lib.d.ts") && typeof text === "string"
    ? text + "\\n// changed host text" : text;
};
""")
    payload, _ = collect(tmp_path, {"a.ts": "export const x = 1;"}, "--semantic", "true", node_preload=preload)
    after = payload["projects"]["MAIN"]["semantic_context"]
    library = next(row for row in after["observations"]
                   if row["operation"] == "readFile" and row["path"].endswith("/lib.d.ts"))
    returned = Path(library["path"]).read_bytes().decode("utf-8-sig") + "\n// changed host text"
    assert library["value"]["text_sha256"] == hashlib.sha256(returned.encode()).hexdigest()
    assert before["compiler"]["installed_module_sha256"] == after["compiler"]["installed_module_sha256"]
    assert before["context_sha256"] != after["context_sha256"]


@pytest.mark.parametrize("link_kind", ["internal", "external", "dangling"])
def test_resolution_does_not_follow_external_or_dangling_links(tmp_path, link_kind):
    root = tmp_path / "repo"
    root.mkdir()
    outside = root / "internal" if link_kind == "internal" else tmp_path / "outside"
    outside.mkdir()
    (outside / "value.ts").write_text("export const x = 1;")
    target = tmp_path / "nonexistent" if link_kind == "dangling" else outside
    try:
        (root / "link").symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Host cannot create symlink fixture: {error}")
    payload, atlas = collect(tmp_path, {"a.ts": 'import { x } from "./link/value"; export const y: string = x;'},
                             "--semantic", "true", project_config={"files": ["a.ts"]})
    context = payload["projects"]["MAIN"]["semantic_context"]
    rows = [row for row in context["observations"] if "/link" in row["path"]]
    assert rows
    if link_kind == "internal":
        assert any(row["allowed"] is True and row["operation"] == "readFile" for row in rows)
        assert any(row["code"] == "TS2322" for row in payload["projects"]["MAIN"]["diagnostics"])
    else:
        assert all(row["allowed"] is False for row in rows)
        assert not any(row["access_status"] == "missing" for row in rows)
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


def test_bound_syntax_and_absent_project_do_not_become_zero_type_errors(tmp_path, checked):
    payload, _ = checked
    assert _ts_diagnostics_by_project(payload) == {}
    result = simulate_dependency_package({"source": "MAIN", "closure_files": []},
                                         {"MAIN": tmp_path}, main_root=tmp_path, ts_totals={})
    component = result["confidence_components"]["typescript_diagnostics"]
    assert component["status"] == "unavailable"
    assert component["score"] is None


@pytest.mark.parametrize("case", ["syntax", "stale_syntax", "semantic", "semantic_stale_source",
                                  "semantic_self_claim"])
def test_frontier_consumes_only_source_bound_collector_findings(tmp_path, monkeypatch, case):
    from tools.engines import react_frontier_intelligence as frontier

    semantic = case.startswith("semantic")
    source = "export const value: string = 42;" if semantic else "\ufeffexport const value = ;\r\n"
    payload, atlas = collect(tmp_path, {"a.ts": source},
        "--semantic", str(semantic).lower(),
        project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}})
    if case in {"stale_syntax", "semantic_stale_source"}:
        atlas["MAIN"]["files"]["a.ts"]["hash"] = "0" * 64
    if semantic:
        assert any(row["code"] == "TS2322" for row in payload["projects"]["MAIN"]["diagnostics"])
    if case == "semantic_self_claim":
        payload["projects"]["MAIN"]["semantic_context"].update(
            snapshot_bound=True, native_project_equivalent=True)
    (tmp_path / "atlas_commit.json").write_text(json.dumps(build_atlas_commit(atlas)), encoding="utf-8")
    monkeypatch.setattr(frontier, "RAW_DIR", tmp_path)
    monkeypatch.setattr(frontier, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(frontier, "load_atlas_data", lambda: atlas)
    monkeypatch.setattr(frontier, "project_runtime_atlas", lambda value: (value, {"analyzed_projects": ["MAIN"]}))
    monkeypatch.setattr(frontier, "_read_project_file", lambda *a: "")
    monkeypatch.setattr(frontier, "analyze_frontier_file", lambda *a: None)
    monkeypatch.setattr(frontier, "_collect_ts_diagnostics", lambda **k: payload)
    monkeypatch.setattr(frontier, "_bundle_evidence", lambda *a: ([], []))
    monkeypatch.setattr(frontier, "_profiler_evidence", lambda *a: ([], []))
    result = frontier.run_react_frontier_intelligence()
    evidence = result["evidence_imports"]["typescript_diagnostics"]
    bound_syntax = case == "syntax"
    assert evidence["snapshot_binding"] == ("COMPLETE" if bound_syntax else "BLOCKED")
    assert bool(result["findings"]) is bound_syntax
    assert bool(evidence["binding_errors"]) is not bound_syntax
    if semantic:
        assert "typescript:MAIN:semantic_context_not_snapshot_bound" in evidence["binding_errors"]
    if case == "semantic_stale_source":
        assert "typescript:MAIN:workspace_read_content_mismatch:a.ts" in evidence["binding_errors"]
    # Use the receipt actually written by Frontier, not a fixture-issued COMPLETE.
    _, usable = evaluate_snapshot_bound_inputs(
        contract_path=CODE_MAPS_DIR / "config/merge_simulation_input_contract.json",
        raw_dir=tmp_path, expected_snapshot_id=build_atlas_commit(atlas)["snapshot_id"],
        payloads={"ts_diagnostics": payload},
    )
    assert ("ts_diagnostics" in usable) is bound_syntax
    totals = _ts_diagnostics_by_project(usable.get("ts_diagnostics", {}))
    merged = simulate_dependency_package({"source": "MAIN", "files": []},
        {"MAIN": tmp_path / "repo"}, main_root=tmp_path, ts_totals=totals)
    component = merged["confidence_components"]["typescript_diagnostics"]
    assert component["status"] == "unavailable"
    assert component["score"] is None
    assert "bind_typescript_diagnostics_to_source_snapshot" in merged["required_actions"]


@pytest.fixture
def compiler_join_model(compiler_plan_context, installed_compiler, monkeypatch, tmp_path):
    """Real join/receiver/adapter; controlled base stages, never native evidence."""
    from types import MappingProxyType, SimpleNamespace
    from tools.core import owned_text_bridge as bridge, typescript_source_binding as binding
    from tools.core.owned_config_input import OwnedConfigInputs
    from tools.tests.fixtures import owned_source_parent_join as join
    c = compiler_plan_context
    base, _, owner = installed_compiler
    repo = CODE_MAPS_DIR
    names = ['config/analysis_snapshot_lineage_contract.json', 'config/third_party_distribution_contract.json',
        'config/schemas/owned_source_parent_preparation.schema.json', 'config/schemas/atlas.schema.json',
        'config/schemas/owned_wire_base.schema.json', 'config/schemas/compiler_library_transport_plan.schema.json',
        'config/source_snapshot_store_policy.json', 'config/schemas/typescript_live_config_context.schema.json',
        'config/schemas/owned_config_extends_observation.schema.json', 'config/schemas/atlas_commit.schema.json']
    metadata = {name: json.loads((repo / name).read_text()) for name in names}
    for name in names[:2]: metadata[name] = json.loads((base / name).read_text())
    profiles = metadata[names[0]]['artifacts']['ts_diagnostics']
    m = SimpleNamespace(context=c, join=join, bridge=bridge, profiles=profiles, loaded=[], events=[],
        adapter_calls=[], receivers=[], disposed=[], source_reads=0, config_reads=0,
        base_error=None, preparation_status='JOIN_PREPARATION_METADATA_CONFORMANT_NOT_EXECUTED',
        schema_error=None, live_result={'state': 'UNAVAILABLE', 'result': None})

    def forbidden(*args, **kwargs):
        raise AssertionError('real process/capture is forbidden in controlled join model')

    def loader(path):
        rel = Path(path).relative_to(base).as_posix()
        m.loaded.append(rel)
        if rel == 'config/schemas/compiler_library_transport_plan.schema.json' and m.schema_error:
            raise m.schema_error
        if rel in metadata: return deepcopy(metadata[rel])
        if rel == owner['runtime_package_path']: return json.loads(Path(path).read_text())
        raise AssertionError('unexpected metadata read: ' + rel)

    def entry(row, *, owned_root):
        m.source_reads += 1
        raw = Path(row['full_path']).read_bytes()  # finite synthetic file, no real target capture
        source = bridge.OwnedSourceInput(raw, raw.decode(), hashlib.sha256(raw).hexdigest(),
            bridge.canonical(profiles['private_owned_text_bridge']))
        return {'_private_owned_source': source, 'content': raw.decode('utf-8-sig')}

    def configs(*, root):
        m.config_reads += 1
        raw = {row['atlas_path']: (root / row['atlas_path']).read_bytes()
            for row in profiles['private_owned_config_extends']['roster'].values()}
        inventory = {'files': {name: {'size_bytes': len(data), 'raw_sha256': hashlib.sha256(data).hexdigest(),
            'host_text_sha256': hashlib.sha256(data.decode('utf-8-sig').encode()).hexdigest()} for name, data in raw.items()}}
        return OwnedConfigInputs(MappingProxyType(raw), bridge.canonical(inventory), 'a' * 64, 'b' * 64)

    class Session:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def request_owned(self, *, source, request_id, wire_base=None, **kwargs):
            m.events.append('source_model')
            if wire_base is not None:
                m.receivers.append(wire_base)
                text, request = source.request(request_id)
                bridge.observe_owned_wire_source(wire_base, (text + '\n').encode(), {
                    'request_id': request_id, 'source_bytes': request['source']['bytes'],
                    'source_sha256': request['source']['sha256'], 'source_policy_sha256': request['policySha256']})
            return json.dumps({'results': {profiles['private_owned_text_bridge']['virtual_file']:
                [{'name': '__file_meta__', 'moduleImports': []}]}}), {'model_only': True}

    def freeze(atlas):
        m.events.append('parent_model')
        if m.base_error is not None: raise m.base_error
        commit = join.build_atlas_commit(atlas)
        return join.ValidatedParent(bridge.canonical(atlas), bridge.canonical(commit), 'f' * 64)

    def child(command, **kwargs):
        assert str(command[1]).endswith('owned_config_extends_harness.cjs')
        m.events.append('config_model')
        expected = json.loads(kwargs['input'])['expected']
        # Controlled fields satisfy old fixture assertions. No process ran and
        # this mocked result must never be retained as a native receipt.
        result = {'status': profiles['private_owned_config_extends']['success_status'], 'code': '',
            'repeated_parse_refused': True, 'disposed_refused': True,
            'strict_observation': {'base': True, 'root_override': None, 'effective': True},
            'reads': {'root': 1, 'base': 1}, 'denied_host_calls': 0, 'root_parse_calls': 1,
            'config_chain_calls': 1, 'cache_entries': 1, 'config_sourcefiles': 2, 'programs': 0,
            'source_worker_parses': 0, 'parent_snapshot_id': expected['atlas_snapshot_id'],
            'atlas_sha256': expected['atlas_sha256'], 'binding_sha256': bridge.policy_identity(expected)}
        output = {'native_config_parser': True, 'modeled_only': False, 'program_attempts': 0,
            'live_io_attempts': 0, 'result': result}
        return SimpleNamespace(returncode=0, stderr=b'', stdout=json.dumps(output).encode())

    def preparation(*args, **kwargs):
        m.events.append('preparation_model')
        return {'status': m.preparation_status}

    original_adapter = binding._plan_owned_compiler_transport_from_wire_base
    def adapter(*args, **kwargs):
        m.events.append('adapter')
        m.adapter_calls.append(kwargs)
        return original_adapter(*args, **kwargs)

    original_dispose = join.dispose_owned_wire_base
    def dispose(receiver):
        m.events.append('receiver_dispose')
        m.disposed.append(receiver)
        return original_dispose(receiver)

    monkeypatch.setattr(join, 'ROOT', base)
    monkeypatch.setattr(join, 'load_json_object_strict', loader)
    monkeypatch.setattr(join, '_load_atlas_source_entry', entry)
    monkeypatch.setattr(join, 'capture_owned_extends_inputs', configs)
    monkeypatch.setattr(join, 'project_owned_path_identity', lambda *a, **k: (True, True))
    monkeypatch.setattr(join, 'NodeAstWorkerSession', Session)
    monkeypatch.setattr(join, 'source_receipt', lambda *a, **k: {'counters': {'model_only': True},
        'loader_restored': True, 'writer_restored': True})
    monkeypatch.setattr(join, '_file_parser_evidence', lambda *a, **k: {'status': 'observed'})
    monkeypatch.setattr(join, '_normalize_polyglot_symbols', lambda *a, **k: [{'name': 'count'}, {'name': 'next'}])
    monkeypatch.setattr(join, '_extract_exports_for_language', lambda *a, **k: ['count', 'next'])
    monkeypatch.setattr(join, 'freeze_source_parent', freeze)
    monkeypatch.setattr(join, 'validate_against_schema', lambda *a, **k: [])
    monkeypatch.setattr(join, 'hold_preparation_context', lambda **kwargs: kwargs)
    monkeypatch.setattr(join, 'check_join_preparation', preparation)
    monkeypatch.setattr(join, 'project_join_live_context', lambda **kwargs: deepcopy(m.live_result))
    monkeypatch.setattr(join, 'dispose_owned_wire_base', dispose)
    monkeypatch.setattr(binding, '_plan_owned_compiler_transport_from_wire_base', adapter)
    monkeypatch.setattr(binding, '_capture_owned_compiler_library_inputs', forbidden)
    monkeypatch.setattr(binding, 'capture_compiler_library_inputs', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', child)
    m.inputs = {'capsule': c['capsule'], 'expected_inventory': deepcopy(c['expected_inventory'])}
    m.run = lambda **kwargs: join.run_join(tmp_path / 'join-model', **kwargs)
    return m


def compiler_join_attachment(model, **kwargs):
    from tools.core.artifact_validator import _validate
    result = model.run(compiler_plan_inputs=model.inputs, include_wire_base=True, **kwargs)
    attachment = result['compiler_transport_plan']
    schema = model.context['schema']
    errors = []
    _validate(attachment, schema['$defs']['join_attachment'], schema, ['attachment'], errors)
    assert not errors
    return result, attachment


@pytest.mark.parametrize('include_package', [False, True])
def test_compiler_join_borrows_owner_and_delegates_once_after_base(compiler_join_model, include_package):
    m = compiler_join_model
    m.inputs['include_package'] = include_package
    result, attachment = compiler_join_attachment(m)
    assert attachment['state'] == 'RESULT_RETURNED' and attachment['code'] == ''
    plan = attachment['result']['plan']
    assert plan['status'] == 'COMPILER_TRANSPORT_SIZE_ONLY_NOT_ADMITTED'
    assert plan['package_included'] is include_package
    assert len(m.adapter_calls) == 1 and m.events.index('preparation_model') < m.events.index('adapter') < m.events.index('receiver_dispose')
    assert m.source_reads == m.config_reads == 1 and len(m.disposed) == 1
    assert m.context['capsule'].metadata() == m.context['expected_inventory']
    assert plan['whole_image_within_limit'] is plan['peak_rss_bytes'] is None
    assert attachment['result']['send_verified'] is attachment['result']['native_execution_attested'] is False
    assert result['production_enabled'] is result['semantic_lineage'] is False
    assert m.loaded.count('config/analysis_snapshot_lineage_contract.json') == 1
    assert m.loaded.count('config/schemas/compiler_library_transport_plan.schema.json') == 1
    assert m.loaded.count('config/schemas/owned_wire_base.schema.json') == 1


@pytest.mark.parametrize('include_live_context', [False, True])
def test_compiler_join_default_keeps_shape_work_and_live_view(compiler_join_model, monkeypatch, include_live_context):
    m = compiler_join_model
    monkeypatch.setattr(m.join, 'pin_join_compiler_plan_inputs', lambda *a: pytest.fail('default pin work'))
    monkeypatch.setattr(m.join, 'project_join_compiler_plan', lambda **k: pytest.fail('default attachment work'))
    result = m.run(include_live_context=include_live_context)
    assert 'compiler_transport_plan' not in result and 'wire_base_measurement' not in result
    assert not m.adapter_calls and not m.receivers and not m.disposed
    assert m.source_reads == m.config_reads == 1
    assert 'config/schemas/compiler_library_transport_plan.schema.json' not in m.loaded
    assert (result.get('live_config_context') == m.live_result) is include_live_context


def test_compiler_join_preserves_live_view_with_returned_plan(compiler_join_model):
    m = compiler_join_model
    result, attachment = compiler_join_attachment(m, include_live_context=True)
    assert result['live_config_context'] == m.live_result and attachment['state'] == 'RESULT_RETURNED'


@pytest.mark.parametrize('kind', ['absent_measurement', 'missing_capsule', 'foreign_capsule', 'source_capsule', 'disposed_capsule', 'bad_flag', 'extra_field'])
def test_compiler_join_refusal_never_recaptures_or_closes_borrowed_owner(compiler_join_model, kind):
    m = compiler_join_model
    if kind == 'missing_capsule': del m.inputs['capsule']
    elif kind == 'foreign_capsule': m.inputs['capsule'] = object()
    elif kind == 'source_capsule':
        from tools.core.owned_text_bridge import OwnedSourceInput, canonical
        raw = b'x'; m.inputs['capsule'] = OwnedSourceInput(raw, 'x', hashlib.sha256(raw).hexdigest(), canonical(m.profiles['private_owned_text_bridge']))
    elif kind == 'disposed_capsule': m.context['capsule'].dispose()
    elif kind == 'bad_flag': m.inputs['include_package'] = 1
    elif kind == 'extra_field': m.inputs['raw'] = 'must not be echoed'
    result = m.run(compiler_plan_inputs=m.inputs, include_wire_base=kind != 'absent_measurement')
    attachment = result['compiler_transport_plan']
    assert attachment['state'] == 'UNAVAILABLE' and attachment['result'] is None
    assert 'must not be echoed' not in json.dumps(attachment)
    assert result['status'] == 'PRIVATE_SYNTHETIC_SOURCE_CONFIG_PARENT_JOIN_OBSERVED_NOT_BOUND'
    if kind != 'disposed_capsule': assert m.context['capsule'].metadata() == m.context['expected_inventory']
    if kind == 'absent_measurement': assert not m.receivers and not m.adapter_calls


@pytest.mark.parametrize('phase', ['parent', 'preparation', 'config'])
def test_compiler_join_base_failure_stays_failed_and_never_plans(compiler_join_model, monkeypatch, phase):
    m = compiler_join_model
    if phase == 'parent': m.base_error = ValueError('join_parent_model_refused')
    elif phase == 'preparation': m.preparation_status = 'BLOCKED_MODEL'
    else: monkeypatch.setattr(m.join.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(ValueError('join_config_model_refused')))
    with pytest.raises(m.join.JoinRefused) as caught:
        m.run(compiler_plan_inputs=m.inputs, include_wire_base=True)
    observation = caught.value.observation
    assert observation['status'] == 'PRIVATE_SOURCE_CONFIG_JOIN_FAILURE_RETAINED'
    assert observation['compiler_transport_plan'] == {'state': 'NOT_ATTEMPTED', 'code': 'join_compiler_plan_base_failed', 'result': None}
    assert not m.adapter_calls and len(m.disposed) == 1
    assert m.context['capsule'].metadata() == m.context['expected_inventory']


@pytest.mark.parametrize('phase', ['pin', 'schema', 'adapter'])
@pytest.mark.parametrize('error', [MemoryError, KeyboardInterrupt])
def test_compiler_join_resource_or_cancel_escapes_optional_stage_through_cleanup(compiler_join_model, monkeypatch, phase, error):
    m = compiler_join_model
    def fail(*args, **kwargs): raise error('private text must not become a base failure receipt')
    if phase == 'pin': monkeypatch.setattr(m.join, 'pin_join_compiler_plan_inputs', fail)
    elif phase == 'schema': m.schema_error = error()
    else: monkeypatch.setattr(m.join.source_binding, '_plan_owned_compiler_transport_from_wire_base', fail)
    with pytest.raises(error): m.run(compiler_plan_inputs=m.inputs, include_wire_base=True)
    assert len(m.disposed) == (0 if phase == 'pin' else 1)
    assert m.context['capsule'].metadata() == m.context['expected_inventory']


@pytest.mark.parametrize('kind', ['compiler', 'inventory', 'receipt', 'binding', 'parent', 'schema', 'incomplete'])
def test_compiler_join_separate_identity_and_schema_refusals_preserve_base(compiler_join_model, monkeypatch, kind):
    m = compiler_join_model
    if kind == 'compiler': m.inputs['expected_inventory']['installed_module_sha256'] = '0' * 64
    elif kind == 'inventory': m.inputs['expected_inventory']['collector_run_id'] = 'other-owner'
    elif kind == 'schema':
        original = m.join.load_json_object_strict
        def drift(path):
            value = original(path)
            if str(path).endswith('compiler_library_transport_plan.schema.json'): value['title'] += ' changed'
            return value
        monkeypatch.setattr(m.join, 'load_json_object_strict', drift)
    elif kind == 'incomplete': monkeypatch.setattr(m.join, 'observe_owned_wire_config', lambda *a: None)
    else:
        original = m.join.project_join_compiler_plan
        def changed(**kwargs):
            if kind == 'receipt':
                kwargs['receipt'] = deepcopy(kwargs['receipt']); kwargs['receipt']['base']['jsonl_bytes'] += 1
            else:
                kwargs['expected_wire'] = dict(kwargs['expected_wire']); kwargs['expected_wire'][kind + ('_sha256' if kind == 'binding' else '_ref_sha256')] = '0' * 64
            return original(**kwargs)
        monkeypatch.setattr(m.join, 'project_join_compiler_plan', changed)
    result, attachment = compiler_join_attachment(m)
    assert attachment['state'] == 'UNAVAILABLE' and attachment['result'] is None
    assert result['status'] == 'PRIVATE_SYNTHETIC_SOURCE_CONFIG_PARENT_JOIN_OBSERVED_NOT_BOUND'
    assert m.context['capsule'].metadata() == m.context['expected_inventory']


@pytest.mark.parametrize('compiler_plan_context', [1600000], indirect=True)
def test_compiler_join_real_synthetic_budget_result_is_forwarded(compiler_join_model):
    _, attachment = compiler_join_attachment(compiler_join_model)
    assert attachment['state'] == 'RESULT_RETURNED'
    assert attachment['result']['plan']['status'] == 'COMPILER_TRANSPORT_BUDGET_EXCEEDED_NOT_ADMITTED'
    assert attachment['result']['plan']['constraints']['draft_wire'] is False


def test_compiler_join_pins_inventory_before_base_and_never_enters_borrowed_owner(compiler_join_model, monkeypatch):
    m = compiler_join_model
    original = m.join._load_atlas_source_entry
    def change_caller(*args, **kwargs):
        m.inputs['expected_inventory']['collector_run_id'] = 'mutated-after-pin'
        return original(*args, **kwargs)
    def forbidden(*args, **kwargs): pytest.fail('join entered/closed borrowed capsule')
    monkeypatch.setattr(m.join, '_load_atlas_source_entry', change_caller)
    with monkeypatch.context() as guard:
        guard.setattr(type(m.context['capsule']), '__enter__', forbidden)
        guard.setattr(type(m.context['capsule']), '__exit__', forbidden)
        guard.setattr(type(m.context['capsule']), 'dispose', forbidden)
        _, attachment = compiler_join_attachment(m)
        assert attachment['state'] == 'RESULT_RETURNED'
    assert m.context['capsule'].metadata() == m.context['expected_inventory']
    m.context['capsule'].dispose()  # actual caller owns this cleanup
    with pytest.raises(ValueError, match='disposed'): m.context['capsule'].metadata()


@pytest.mark.parametrize('kind', ['promoted', 'raw', 'unavailable', 'null_plan', 'null_reference', 'unavailable_plan'])
def test_compiler_join_revalidates_adapter_output(compiler_join_model, monkeypatch, kind):
    m = compiler_join_model
    original = m.join.source_binding._plan_owned_compiler_transport_from_wire_base
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if kind == 'promoted': result['send_verified'] = True
        elif kind == 'raw': result['raw'] = 'do not persist'
        elif kind == 'null_plan': result['plan'] = None
        elif kind == 'null_reference': result['receipt_sha256'] = None
        elif kind == 'unavailable_plan': result['plan']['status'] = 'COMPILER_TRANSPORT_PLAN_UNAVAILABLE'
        else: result['status'] = 'WIRE_BASE_COMPILER_PLAN_UNAVAILABLE'
        return result
    monkeypatch.setattr(m.join.source_binding, '_plan_owned_compiler_transport_from_wire_base', changed)
    _, attachment = compiler_join_attachment(m)
    assert attachment['state'] == 'UNAVAILABLE' and attachment['result'] is None
    assert 'do not persist' not in json.dumps(attachment)


@pytest.mark.parametrize('kind', ['returned_null', 'not_attempted_code', 'unavailable_with_result', 'unavailable_adapter', 'raw', 'wrong_state'])
def test_compiler_join_schema_refuses_inconsistent_attachment_states(compiler_join_model, kind):
    from tools.core.artifact_validator import _validate
    m = compiler_join_model
    if kind == 'returned_null': candidate = {'state': 'RESULT_RETURNED', 'code': '', 'result': None}
    elif kind == 'not_attempted_code': candidate = {'state': 'NOT_ATTEMPTED', 'code': 'join_compiler_plan_inputs_unavailable', 'result': None}
    else:
        _, candidate = compiler_join_attachment(m)
        if kind == 'unavailable_with_result': candidate.update(state='UNAVAILABLE', code='join_compiler_plan_inputs_unavailable')
        elif kind == 'unavailable_adapter': candidate['result']['status'] = 'WIRE_BASE_COMPILER_PLAN_UNAVAILABLE'
        elif kind == 'raw': candidate['raw'] = 'forbidden'
        else: candidate['state'] = 'NATIVE_PASS'
    errors = []
    schema = m.context['schema']
    _validate(candidate, schema['$defs']['join_attachment'], schema, ['attachment'], errors)
    assert errors


@pytest.mark.parametrize('outcome', ['success', 'base_failure', 'optional_refusal', 'resource', 'cancellation'])
def test_compiler_join_external_caller_closes_its_owner_on_each_exit(compiler_join_model, monkeypatch, outcome):
    m = compiler_join_model
    if outcome == 'base_failure': m.base_error = ValueError('join_model_parent_failure')
    elif outcome == 'optional_refusal': m.inputs['expected_inventory']['collector_run_id'] = 'foreign'
    elif outcome in ('resource', 'cancellation'):
        def fail(*args, **kwargs): raise (MemoryError if outcome == 'resource' else KeyboardInterrupt)
        monkeypatch.setattr(m.join.source_binding, '_plan_owned_compiler_transport_from_wire_base', fail)
    expected_error = {'base_failure': m.join.JoinRefused, 'resource': MemoryError, 'cancellation': KeyboardInterrupt}.get(outcome)
    with m.context['capsule']:
        if expected_error is not None:
            with pytest.raises(expected_error): m.run(compiler_plan_inputs=m.inputs, include_wire_base=True)
        else:
            result = m.run(compiler_plan_inputs=m.inputs, include_wire_base=True)
            assert result['compiler_transport_plan']['state'] == ('UNAVAILABLE' if outcome == 'optional_refusal' else 'RESULT_RETURNED')
        assert m.context['capsule'].metadata() == m.context['expected_inventory']
    with pytest.raises(ValueError, match='disposed'): m.context['capsule'].metadata()
    assert len(m.disposed) == 1


@pytest.mark.parametrize('kind', ['returned_null_plan', 'returned_null_receipt', 'returned_null_context',
    'returned_error_code', 'returned_unavailable_plan', 'returned_inner_error',
    'unavailable_plan', 'unavailable_receipt', 'unavailable_context', 'unavailable_empty_code'])
def test_wire_adapter_state_schema_rejects_inconsistent_metadata(wire_plan_context, kind):
    """Mutate real producer output; these counterexamples are not execution evidence."""
    from tools.core.artifact_validator import _validate
    c = wire_plan_context
    result = wire_plan_call(c)
    held_plan = result['plan']
    if kind.startswith('unavailable_'):
        result.update(status='WIRE_BASE_COMPILER_PLAN_UNAVAILABLE', code='wire_plan_owner_unavailable',
            receipt_sha256=None, context_sha256=None, plan=None)
        if kind == 'unavailable_plan': result['plan'] = held_plan
        elif kind == 'unavailable_receipt': result['receipt_sha256'] = 'a' * 64
        elif kind == 'unavailable_context': result['context_sha256'] = 'a' * 64
        else: result['code'] = ''
    elif kind == 'returned_null_plan': result['plan'] = None
    elif kind == 'returned_null_receipt': result['receipt_sha256'] = None
    elif kind == 'returned_null_context': result['context_sha256'] = None
    elif kind == 'returned_error_code': result['code'] = 'wire_plan_owner_unavailable'
    elif kind == 'returned_unavailable_plan': result['plan']['status'] = 'COMPILER_TRANSPORT_PLAN_UNAVAILABLE'
    else: result['plan']['code'] = 'compiler_plan_owner_unavailable'
    schema = c['schemas']['plan']
    errors = []
    _validate(result, schema['$defs']['wire_adapter'], schema, ['adapter_state'], errors)
    assert errors
    errors = []
    _validate({'state': 'RESULT_RETURNED', 'code': '', 'result': result}, schema['$defs']['join_attachment'],
        schema, ['transitive_attachment'], errors)
    assert errors


@pytest.mark.parametrize('flag', ['base_content_verified', 'send_verified', 'native_execution_attested',
    'production_enabled', 'semantic_lineage', 'cache_eligible'])
def test_wire_adapter_state_schema_keeps_admission_false(wire_plan_context, flag):
    from tools.core.artifact_validator import _validate
    c = wire_plan_context
    result = wire_plan_call(c)
    result[flag] = True
    errors = []
    schema = c['schemas']['plan']
    _validate(result, schema['$defs']['wire_adapter'], schema, ['adapter_state'], errors)
    assert errors
    errors = []
    _validate({'state': 'RESULT_RETURNED', 'code': '', 'result': result}, schema['$defs']['join_attachment'],
        schema, ['transitive_attachment'], errors)
    assert errors


@pytest.fixture
def stream_layout_context(installed_compiler, monkeypatch, request):
    """Finite fake installation and a real borrowed capsule; never native capture."""
    from tools.core.typescript_source_binding import _capture_owned_compiler_library_inputs
    base, library, _ = installed_compiler
    case = getattr(request, 'param', 'small')
    samples = {'zero': b'', 'one': b'x', 'two': b'xy', 'bom': b'\xef\xbb\xbf',
        'multibyte': b'\xef\xbb\xbf' + ('😀\r\n' * 2000).encode(), 'large': b'x' * 1600100}
    if case in samples: (library / 'lib.d.ts').write_bytes(samples[case])
    if case == 'batch':
        for name, raw in {'lib.d.ts': b'a' * 100, 'lib.one.d.ts': b'b' * 97, 'lib.two.d.ts': b'c' * 110}.items():
            (library / name).write_bytes(raw)
    def forbidden(*args, **kwargs): raise AssertionError('no native process in stream layout controls')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    expected, capsule = _capture_owned_compiler_library_inputs(base, 'synthetic-stream-layout')
    assert capsule is not None
    profiles = json.loads((base / 'config/analysis_snapshot_lineage_contract.json').read_text())['artifacts']['ts_diagnostics']
    if case in ('multibyte', 'batch'): profiles['private_owned_config_handoff']['max_request_bytes'] = 4096
    schema = json.loads((CODE_MAPS_DIR / profiles['private_compiler_stream_layout']['schema']).read_text())
    yield {'capsule': capsule, 'expected_inventory': expected, 'context': {'session_id': 'stream-layout', 'owner_context_sha256': 'a' * 64},
        'profiles': profiles, 'schema': schema, 'case': case}
    capsule.dispose()


def stream_layout_call(fixture, **changes):
    from tools.core.typescript_source_binding import _plan_owned_compiler_stream_layout
    from tools.core.artifact_validator import _validate
    args = {key: fixture[key] for key in ('expected_inventory', 'context', 'profiles', 'schema')}
    capsule = changes.pop('capsule', fixture['capsule']); args.update(changes)
    result = _plan_owned_compiler_stream_layout(capsule, **args)
    errors = []
    _validate(result, fixture['schema']['$defs']['stream_result'], fixture['schema'], ['stream_layout'], errors)
    assert not errors
    return result


def actual_synthetic_stream_frames(result, libraries):
    """Independent full encoding oracle in tests only; never used by the planner."""
    import base64
    encode = lambda body: (json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n').encode()
    manifest = result['manifest']
    manifest_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    assert manifest_hash == result['manifest_sha256']
    common = {'version': manifest['version'], 'session_id': manifest['session_id'], 'manifest_sha256': manifest_hash}
    frames = [encode({**common, 'kind': 'open', 'seq': 0, 'manifest': manifest})]
    reconstructed = [bytearray() for _ in manifest['files']]
    for frame in manifest['layout']:
        rows = []
        for slot in frame['records']:
            index, offset, length = (slot[key] for key in ('file_index', 'offset', 'raw_bytes'))
            assert offset == len(reconstructed[index]) and length > 0
            raw = libraries[manifest['files'][index]['name']][offset:offset + length]
            assert len(raw) == length
            reconstructed[index].extend(raw)
            rows.append({**slot, 'chunk_sha256': hashlib.sha256(raw).hexdigest(),
                'utf8Base64': base64.b64encode(raw).decode('ascii')})
        frames.append(encode({**common, 'kind': 'data', 'seq': frame['seq'], 'records': rows}))
    transcript = hashlib.sha256(b''.join(frames)).hexdigest()
    frames.append(encode({**common, 'kind': 'commit', 'seq': len(frames),
        'files_complete': len(manifest['files']), 'raw_bytes_total': result['library_raw_bytes'], 'transcript_sha256': transcript}))
    for row, raw in zip(manifest['files'], reconstructed):
        assert bytes(raw) == libraries[row['name']] and hashlib.sha256(raw).hexdigest() == row['raw_sha256']
    return frames


@pytest.mark.parametrize('stream_layout_context', ['small', 'zero', 'one', 'two', 'bom', 'multibyte', 'batch', 'large'], indirect=True)
def test_stream_layout_exact_size_matches_independent_synthetic_encoding(stream_layout_context):
    c = stream_layout_context
    result = stream_layout_call(c)
    assert result['status'] == 'COMPILER_STREAM_LAYOUT_ONLY_NOT_ADMITTED'
    assert all(value is True for value in result['constraints'].values())
    actual = actual_synthetic_stream_frames(result, c['capsule'].library_bytes())
    assert [len(frame) for frame in actual] == [row['jsonl_bytes'] for row in result['frames']]
    assert max(map(len, actual)) == result['largest_frame_jsonl_bytes'] <= result['frame_limit_bytes']
    assert sum(map(len, actual)) == result['wire_bytes_total']
    assert result['whole_image_within_limit'] is result['peak_rss_bytes'] is None
    assert result['package_included'] is result['raw_frame_emitted'] is result['send_verified'] is False
    assert 'utf8Base64' not in json.dumps(result) and 'chunk_sha256' not in json.dumps(result)
    if c['case'] == 'large':
        assert result['chunked_files'] == 1 and result['wire_bytes_total'] > result['frame_limit_bytes']
    elif c['case'] == 'batch':
        assert len(result['manifest']['layout']) == 1 and result['record_count'] == 3
    elif c['case'] == 'zero': assert result['record_count'] == 0 and len(result['frames']) == 2
    assert c['capsule'].metadata() == c['expected_inventory']


def test_stream_layout_no_io_encoding_or_borrowed_owner_close(stream_layout_context, monkeypatch):
    import base64, builtins
    c = stream_layout_context
    def forbidden(*a, **k): raise AssertionError('metadata-only plan performed IO/base64/disposal')
    with monkeypatch.context() as guard:
        for owner, name in [(Path, 'open'), (Path, 'resolve'), (os, 'scandir'), (builtins, 'open'), (base64, 'b64encode'),
            (type(c['capsule']), '__enter__'), (type(c['capsule']), '__exit__'), (type(c['capsule']), 'dispose')]:
            guard.setattr(owner, name, forbidden)
        result = stream_layout_call(c)
    assert result['status'] == 'COMPILER_STREAM_LAYOUT_ONLY_NOT_ADMITTED'
    assert c['capsule'].metadata() == c['expected_inventory']


def test_stream_layout_exact_open_boundary_and_changed_manifest_context(stream_layout_context):
    c = stream_layout_context
    first = stream_layout_call(c)
    needed = first['frames'][0]['jsonl_bytes']
    profiles = deepcopy(c['profiles']); profiles['private_owned_config_handoff']['max_request_bytes'] = needed
    at_limit = stream_layout_call(c, profiles=profiles)
    profiles['private_owned_config_handoff']['max_request_bytes'] -= 1
    over = stream_layout_call(c, profiles=profiles)
    assert at_limit['status'] == 'COMPILER_STREAM_LAYOUT_ONLY_NOT_ADMITTED'
    assert at_limit['frames'][0]['jsonl_bytes'] == needed
    assert over['status'] == 'COMPILER_STREAM_LAYOUT_BUDGET_EXCEEDED_NOT_ADMITTED'
    assert over['constraints']['open_wire'] is False and over['budget_reason'] == 'open_wire'
    changed = deepcopy(c['context']); changed['owner_context_sha256'] = 'b' * 64
    assert stream_layout_call(c, context=changed)['manifest_sha256'] != first['manifest_sha256']


@pytest.mark.parametrize('kind', ['foreign', 'missing', 'disposed', 'uninitialized'])
def test_stream_layout_refuses_invalid_owner(stream_layout_context, kind):
    from tools.core.typescript_source_binding import _CompilerLibraryCapsule
    c = stream_layout_context
    owner = object() if kind == 'foreign' else None if kind == 'missing' else c['capsule']
    if kind == 'disposed': owner.dispose()
    elif kind == 'uninitialized': owner = object.__new__(_CompilerLibraryCapsule)
    result = stream_layout_call(c, capsule=owner)
    assert result['status'] == 'COMPILER_STREAM_LAYOUT_UNAVAILABLE' and result['code'] == 'stream_plan_owner_unavailable'


@pytest.mark.parametrize('kind', ['inventory', 'bytes', 'context', 'session', 'promoted', 'version', 'schema', 'metadata', 'bool_limit'])
def test_stream_layout_refuses_changed_or_unknown_inputs(stream_layout_context, kind):
    c = stream_layout_context
    inventory, context, profiles, schema = (deepcopy(c[key]) for key in ('expected_inventory', 'context', 'profiles', 'schema'))
    if kind == 'inventory': inventory['collector_run_id'] = 'other'
    elif kind == 'bytes': c['capsule']._libraries = (('lib.d.ts', b'changed'),)
    elif kind == 'context': context['owner_context_sha256'] = 'not-a-hash'
    elif kind == 'session': context['session_id'] = 'x' * 65
    elif kind == 'promoted': profiles['private_compiler_stream_layout']['raw_transport_enabled'] = True
    elif kind == 'version': profiles['private_compiler_stream_layout']['version'] = 'future'
    elif kind == 'schema': schema['title'] += ' changed'
    elif kind == 'metadata': profiles['private_input_image_experiment']['limits']['metadata_bytes'] = 1
    else: profiles['private_input_image_experiment']['limits']['compiler_files'] = True
    result = stream_layout_call(c, expected_inventory=inventory, context=context, profiles=profiles, schema=schema)
    assert result['status'] == 'COMPILER_STREAM_LAYOUT_UNAVAILABLE'
    assert result['manifest'] is None and result['frames'] is None


@pytest.mark.parametrize('budget', ['library_file', 'capture_total', 'image_total', 'image_file', 'image_content', 'wire'])
def test_stream_layout_separates_known_budget_excess_from_unavailable(stream_layout_context, budget):
    c = stream_layout_context
    profiles = deepcopy(c['profiles']); image = profiles['private_input_image_experiment']['limits']
    if budget == 'library_file': profiles['compiler_library_inputs']['max_file_bytes'] = 1; key = 'library_file_raw'
    elif budget == 'capture_total': profiles['compiler_library_inputs']['max_total_bytes'] = 1; key = 'capture_library_raw_lower_bound'
    elif budget == 'image_total': image['compiler_bytes'] = 1; key = 'image_compiler_raw'
    elif budget == 'image_file': image['compiler_file_bytes'] = 1; key = 'image_compiler_file_raw'
    elif budget == 'image_content': image['image_bytes'] = 1; key = 'image_content_lower_bound'
    else: profiles['private_owned_config_handoff']['max_request_bytes'] = 1; key = 'data_wire'
    result = stream_layout_call(c, profiles=profiles)
    assert result['status'] == 'COMPILER_STREAM_LAYOUT_BUDGET_EXCEEDED_NOT_ADMITTED'
    assert result['constraints'][key] is False and result['budget_reason'] == key
    assert result['code'] == '' and result['current_reader_supported'] is False


@pytest.mark.parametrize('stream_layout_context', ['batch'], indirect=True)
@pytest.mark.parametrize('budget', ['capture_count', 'image_count'])
def test_stream_layout_count_limits_are_separate(stream_layout_context, budget):
    c = stream_layout_context; profiles = deepcopy(c['profiles'])
    if budget == 'capture_count': profiles['compiler_library_inputs']['max_files'] = 1; key = 'library_count'
    else: profiles['private_input_image_experiment']['limits']['compiler_files'] = 1; key = 'image_compiler_count'
    assert stream_layout_call(c, profiles=profiles)['constraints'][key] is False


def test_stream_layout_recipe_is_maximal_and_caller_copies_are_independent(stream_layout_context):
    from tools.core.typescript_source_binding import _compiler_stream_jsonl_size
    c = stream_layout_context
    profiles = deepcopy(c['profiles']); profiles['private_owned_config_handoff']['max_request_bytes'] = 4096
    # A controlled owned synthetic file large enough for multiple bounded ranges.
    raw = b'x' * 10000; c['capsule']._libraries = (('lib.d.ts', raw),)
    metadata = c['capsule'].metadata(); metadata['files']['lib.d.ts']['sha256'] = hashlib.sha256(raw).hexdigest()
    metadata['files']['lib.d.ts']['host_text_sha256'] = hashlib.sha256(raw).hexdigest()
    c['capsule']._metadata_json = json.dumps(metadata)
    result = stream_layout_call(c, expected_inventory=metadata, profiles=profiles)
    assert result['status'] == 'COMPILER_STREAM_LAYOUT_ONLY_NOT_ADMITTED'
    actual_synthetic_stream_frames(result, c['capsule'].library_bytes())
    frame_context = {'version': result['manifest']['version'], 'session_id': result['manifest']['session_id']}
    for frame in result['manifest']['layout'][:-1]:
        rows = deepcopy(frame['records']); rows[-1]['raw_bytes'] += 1
        assert _compiler_stream_jsonl_size('data', frame_context, result['manifest_sha256'], frame['seq'], records=rows) > 4096
    result['manifest']['files'][0]['bytes'] = -1
    assert stream_layout_call(c, expected_inventory=metadata, profiles=profiles)['manifest']['files'][0]['bytes'] == len(raw)


@pytest.mark.parametrize('error', [MemoryError, KeyboardInterrupt])
def test_stream_layout_resource_failure_and_cancellation_propagate(stream_layout_context, monkeypatch, error):
    from tools.core import typescript_source_binding as binding
    def fail(*a, **k): raise error
    monkeypatch.setattr(binding, '_compiler_stream_data_layout', fail)
    with pytest.raises(error): stream_layout_call(stream_layout_context)


def test_stream_layout_planning_deadline_and_owner_change_are_not_success(stream_layout_context, monkeypatch):
    import time
    from tools.core import typescript_source_binding as binding
    c = stream_layout_context
    clock = iter([0, 31])
    with monkeypatch.context() as guard:
        guard.setattr(time, 'monotonic', lambda: next(clock, 31))
        result = stream_layout_call(c)
    assert result['code'] == 'stream_plan_time_budget'
    original = binding._compiler_stream_data_layout
    def dispose(*args, **kwargs):
        result = original(*args, **kwargs); c['capsule'].dispose(); return result
    monkeypatch.setattr(binding, '_compiler_stream_data_layout', dispose)
    result = stream_layout_call(c)
    assert result['code'] == 'stream_plan_owner_changed' and result['manifest'] is None


@pytest.mark.parametrize('kind', ['promoted', 'success_without_manifest', 'budget_without_false_constraint', 'raw'])
def test_stream_layout_schema_refuses_promoted_or_inconsistent_results(stream_layout_context, kind):
    from tools.core.artifact_validator import _validate
    c = stream_layout_context; result = stream_layout_call(c)
    if kind == 'promoted': result['send_verified'] = True
    elif kind == 'success_without_manifest': result['manifest'] = None
    elif kind == 'raw': result['raw'] = 'not a report field'
    else: result.update(status='COMPILER_STREAM_LAYOUT_BUDGET_EXCEEDED_NOT_ADMITTED', budget_reason='open_wire')
    errors = []
    _validate(result, c['schema']['$defs']['stream_result'], c['schema'], ['stream_layout'], errors)
    assert errors


def test_stream_layout_refuses_oversized_recipe_without_raw_encoding(stream_layout_context):
    from tools.core.typescript_source_binding import _semantic_metadata_json
    c = stream_layout_context; profiles = deepcopy(c['profiles'])
    raw = b'x' * 1000000
    c['capsule']._libraries = (('lib.d.ts', raw),)
    inventory = c['capsule'].metadata()
    inventory['files']['lib.d.ts'].update(sha256=hashlib.sha256(raw).hexdigest(), host_text_sha256=hashlib.sha256(raw).hexdigest())
    c['capsule']._metadata_json = json.dumps(inventory)
    profiles['private_owned_config_handoff']['max_request_bytes'] = 400
    # The inputs fit. A tiny frame would require a recipe larger than that cap.
    initial = len(_semantic_metadata_json({'profiles': profiles, 'schema': c['schema'],
        'inventory': inventory, 'context': c['context']}).encode())
    profiles['private_input_image_experiment']['limits']['metadata_bytes'] = initial + 1024
    result = stream_layout_call(c, profiles=profiles, expected_inventory=inventory)
    assert result['status'] == 'COMPILER_STREAM_LAYOUT_BUDGET_EXCEEDED_NOT_ADMITTED'
    assert result['budget_reason'] == 'manifest_metadata' and result['constraints']['manifest_metadata'] is False
    assert result['manifest'] is None and result['frames'] is None and result['wire_bytes_total'] is None


@pytest.mark.parametrize('flag', ['current_reader_supported', 'raw_frame_emitted', 'raw_content_verified',
    'base_content_verified', 'send_verified', 'source_freshness_verified', 'snapshot_bound',
    'loaded_code_attested', 'native_execution_attested', 'production_enabled', 'semantic_lineage', 'cache_eligible'])
def test_stream_layout_schema_keeps_each_admission_false(stream_layout_context, flag):
    from tools.core.artifact_validator import _validate
    c = stream_layout_context; result = stream_layout_call(c); result[flag] = True
    errors = []
    _validate(result, c['schema']['$defs']['stream_result'], c['schema'], ['stream_layout'], errors)
    assert errors


@pytest.mark.parametrize('stream_layout_context', ['zero'], indirect=True)
def test_stream_layout_empty_file_still_obeys_shared_deadline(stream_layout_context, monkeypatch):
    import time
    clock = iter([0, 31])
    monkeypatch.setattr(time, 'monotonic', lambda: next(clock, 31))
    result = stream_layout_call(stream_layout_context)
    assert result['status'] == 'COMPILER_STREAM_LAYOUT_UNAVAILABLE'
    assert result['code'] == 'stream_plan_time_budget' and result['manifest'] is None


def encoder_start(fixture, plan=None, **changes):
    import time
    from tools.core.typescript_source_binding import _begin_owned_compiler_stream_encoder
    plan = stream_layout_call(fixture) if plan is None else plan
    args = {k:fixture[k] for k in ('expected_inventory','context','profiles','schema')}
    args.update(plan=plan,expected_manifest_sha256=plan['manifest_sha256'],deadline=time.monotonic()+20)
    capsule=changes.pop('capsule',fixture['capsule']);args.update(changes)
    return _begin_owned_compiler_stream_encoder(capsule,**args)


def encoder_snapshot(encoder, fixture):
    from tools.core.artifact_validator import _validate
    result=encoder.snapshot();errors=[]
    _validate(result,fixture['schema']['$defs']['encoder_snapshot'],fixture['schema'],['encoder_snapshot'],errors)
    assert not errors
    return result


def encoder_ack(frame, state=None):
    body=json.loads(frame)
    return {k:body[k] for k in ('version','session_id','manifest_sha256','seq')} | {
        'frame_sha256':hashlib.sha256(frame).hexdigest(),'state':state or ('COMPLETE' if body['kind']=='commit' else 'STAGED')}


def encoder_drain(encoder, fixture):
    result=[]
    while encoder_snapshot(encoder,fixture)['state']!='COMPLETE':
        frame=encoder.next_frame();result.append(frame)
        if json.loads(frame)['kind']=='commit': encoder.mark_input_closed()
        encoder.accept_ack(encoder_ack(frame))
    return result


@pytest.mark.parametrize('stream_layout_context',['small','zero','one','two','bom','multibyte','batch','large'],indirect=True)
def test_stream_encoder_matches_independent_frames_and_transcript(stream_layout_context):
    c=stream_layout_context;plan=stream_layout_call(c)
    oracle=actual_synthetic_stream_frames(plan,c['capsule'].library_bytes())
    with encoder_start(c,plan) as encoder:
        frames=encoder_drain(encoder,c)
        assert frames==oracle and all(type(frame) is bytes for frame in frames)
        result=encoder_snapshot(encoder,c)
        assert result['frames_encoded']==len(frames) and result['encoded_bytes']==sum(map(len,frames))==plan['wire_bytes_total']
        assert result['state']=='COMPLETE' and result['raw_frame_encoded'] is True
        assert result['outstanding_frame_sha256'] is None and result['raw_frame_emitted'] is result['send_verified'] is False
        assert result['whole_image_within_limit'] is result['peak_rss_bytes'] is None
        assert encoder._capsule is encoder._pending is encoder._held is None
    assert encoder_snapshot(encoder,c)['state']=='DISPOSED'
    assert c['capsule'].metadata()==c['expected_inventory']


@pytest.mark.parametrize('kind',['status','promoted','manifest_pin','context','inventory','bytes','schema','policy',
    'offset','hole','index','sequence','row_size','frame_size','total','count','coverage','extra_record'])
def test_stream_encoder_refuses_changed_or_inconsistent_plan(stream_layout_context,kind):
    from tools.core.typescript_source_binding import _CompilerStreamEncoderRefused,_semantic_metadata_sha
    c=stream_layout_context;plan=stream_layout_call(c);changes={}
    if kind=='status': plan['status']='COMPILER_STREAM_LAYOUT_BUDGET_EXCEEDED_NOT_ADMITTED'
    elif kind=='promoted': plan['send_verified']=True
    elif kind=='manifest_pin': changes['expected_manifest_sha256']='b'*64
    elif kind=='context': changes['context']={**c['context'],'owner_context_sha256':'b'*64}
    elif kind=='inventory': changes['expected_inventory']={**c['expected_inventory'],'collector_run_id':'changed'}
    elif kind=='bytes': c['capsule']._libraries=(('lib.d.ts',b'changed'),)
    elif kind=='schema': changes['schema']=deepcopy(c['schema']);changes['schema']['title']+=' changed'
    elif kind=='policy': changes['profiles']=deepcopy(c['profiles']);changes['profiles']['private_compiler_stream_encoder']['raw_transport_enabled']=True
    elif kind=='frame_size': plan['frames'][0]['jsonl_bytes']+=1
    elif kind=='total': plan['wire_bytes_total']+=1
    elif kind=='count': plan['record_count']+=1
    else:
        row=plan['manifest']['layout'][0];record=row['records'][0]
        if kind=='offset': record['offset']=-1
        elif kind=='hole': record['offset']=1
        elif kind=='index': record['file_index']=len(plan['manifest']['files'])
        elif kind=='sequence': row['seq']+=1
        elif kind=='row_size': row['jsonl_bytes']+=1
        elif kind=='coverage': record['raw_bytes']-=1
        else: row['records'].append(deepcopy(record))
        plan['manifest_sha256']=_semantic_metadata_sha(plan['manifest'])
    with pytest.raises(_CompilerStreamEncoderRefused,match='^stream_encoder_'):
        encoder_start(c,plan,**changes)


@pytest.mark.parametrize('kind',['foreign','missing','disposed','uninitialized'])
def test_stream_encoder_refuses_unavailable_owner(stream_layout_context,kind):
    from tools.core.typescript_source_binding import _CompilerStreamEncoderRefused,_CompilerLibraryCapsule
    c=stream_layout_context;plan=stream_layout_call(c)
    owner=c['capsule']
    if kind=='foreign': owner=object()
    elif kind=='missing': owner=None
    elif kind=='disposed': owner.dispose()
    else: owner=object.__new__(_CompilerLibraryCapsule)
    with pytest.raises(_CompilerStreamEncoderRefused,match='stream_encoder_plan_invalid'):
        encoder_start(c,plan,capsule=owner)


@pytest.mark.parametrize('deadline',[-1,True,float('nan'),float('inf'),1e100])
def test_stream_encoder_requires_finite_bounded_deadline(stream_layout_context,deadline):
    from tools.core.typescript_source_binding import _CompilerStreamEncoderRefused
    with pytest.raises(_CompilerStreamEncoderRefused,match='stream_encoder_deadline'):
        encoder_start(stream_layout_context,deadline=deadline)


@pytest.mark.parametrize('kind',['session_id','manifest_sha256','seq','frame_sha256','state','version','extra','null','bool_seq','oversized'])
def test_stream_encoder_ack_mismatch_aborts_and_clears(stream_layout_context,kind):
    from tools.core.typescript_source_binding import _CompilerStreamEncoderRefused
    c=stream_layout_context;encoder=encoder_start(c);frame=encoder.next_frame();ack=encoder_ack(frame)
    if kind in ('manifest_sha256','frame_sha256'): ack[kind]='b'*64
    elif kind=='seq': ack['seq']+=1
    elif kind=='bool_seq': ack['seq']=False
    elif kind=='state': ack['state']='COMPLETE'
    elif kind=='version': ack['version']='future'
    elif kind=='session_id': ack['session_id']='foreign'
    elif kind=='extra': ack['raw']='forbidden'
    elif kind=='oversized': ack['raw']='x'*(c['profiles']['private_input_image_experiment']['limits']['metadata_bytes']+1)
    else: ack=None
    with pytest.raises(_CompilerStreamEncoderRefused,match='stream_encoder_ack_invalid'):
        encoder.accept_ack(ack)
    result=encoder_snapshot(encoder,c)
    assert result['state']=='ABORTED' and encoder._pending is encoder._held is encoder._capsule is None
    assert c['capsule'].metadata()==c['expected_inventory']


@pytest.mark.parametrize('kind',['ack_before_frame','next_without_ack','close_early','ack_before_close','close_twice','next_after_complete'])
def test_stream_encoder_credit_and_commit_close_order(stream_layout_context,kind):
    from tools.core.typescript_source_binding import _CompilerStreamEncoderRefused
    c=stream_layout_context;encoder=encoder_start(c)
    if kind=='ack_before_frame': action=lambda:encoder.accept_ack({})
    elif kind=='close_early': action=encoder.mark_input_closed
    elif kind=='next_without_ack': encoder.next_frame();action=encoder.next_frame
    elif kind=='next_after_complete': encoder_drain(encoder,c);action=encoder.next_frame
    else:
        while True:
            frame=encoder.next_frame()
            if json.loads(frame)['kind']=='commit': break
            encoder.accept_ack(encoder_ack(frame))
        if kind=='ack_before_close': action=lambda:encoder.accept_ack(encoder_ack(frame))
        else: encoder.mark_input_closed();action=encoder.mark_input_closed
    with pytest.raises(_CompilerStreamEncoderRefused,match='stream_encoder_order'):action()
    assert encoder_snapshot(encoder,c)['state']=='ABORTED' and encoder._pending is None


def test_stream_encoder_no_io_recapture_or_borrowed_owner_close(stream_layout_context,monkeypatch):
    import builtins
    from tools.core import typescript_source_binding as binding
    c=stream_layout_context;plan=stream_layout_call(c)
    def forbidden(*args,**kwargs):raise AssertionError('encoder performed IO/recapture/capsule disposal')
    with monkeypatch.context() as guard:
        for owner,name in [(Path,'open'),(Path,'resolve'),(builtins,'open'),(os,'scandir'),
            (binding,'_capture_owned_compiler_library_inputs'),(binding,'_compiler_stream_data_layout'),(type(c['capsule']),'dispose')]:
            guard.setattr(owner,name,forbidden)
        with encoder_start(c,plan) as encoder: frames=encoder_drain(encoder,c)
    assert len(frames)>=2 and c['capsule'].metadata()==c['expected_inventory']


def test_stream_encoder_partial_write_borrows_one_immutable_frame(stream_layout_context):
    c=stream_layout_context
    with encoder_start(c) as encoder:
        frame=encoder.next_frame();pieces=[memoryview(frame)[:7],memoryview(frame)[7:]]
        assert b''.join(pieces)==frame and all(piece.obj is encoder._pending for piece in pieces)
        assert encoder_snapshot(encoder,c)['frames_encoded']==1
        encoder.accept_ack(encoder_ack(frame))
        assert encoder._pending is None and encoder_snapshot(encoder,c)['state']=='READY'


@pytest.mark.parametrize('kind',['disposed','changed'])
def test_stream_encoder_rechecks_borrowed_owner_between_calls(stream_layout_context,kind):
    from tools.core.typescript_source_binding import _CompilerStreamEncoderRefused
    c=stream_layout_context;encoder=encoder_start(c);frame=encoder.next_frame()
    if kind=='disposed': c['capsule'].dispose()
    else: c['capsule']._libraries=(('lib.d.ts',b'changed'),)
    with pytest.raises(_CompilerStreamEncoderRefused,match='stream_encoder_owner_'):
        encoder.accept_ack(encoder_ack(frame))
    assert encoder._pending is encoder._capsule is encoder._held is None


@pytest.mark.parametrize('error',[MemoryError,KeyboardInterrupt])
def test_stream_encoder_resource_failure_cleans_and_propagates(stream_layout_context,monkeypatch,error):
    c=stream_layout_context;encoder=encoder_start(c)
    def fail(self):raise error
    monkeypatch.setattr(type(encoder),'_frame',fail)
    with pytest.raises(error):encoder.next_frame()
    assert encoder._pending is encoder._capsule is encoder._held is None
    assert encoder_snapshot(encoder,c)['code']=='stream_encoder_interrupted'
    assert c['capsule'].metadata()==c['expected_inventory']


@pytest.mark.parametrize('stage',['begin','next','close','final_ack','after_encoding'])
@pytest.mark.parametrize('stream_layout_context',['zero'],indirect=True)
def test_stream_encoder_one_deadline_including_zero_data_terminal_paths(stream_layout_context,monkeypatch,stage):
    import time
    from tools.core.typescript_source_binding import _CompilerStreamEncoderRefused
    c=stream_layout_context;clock=[0.0]
    monkeypatch.setattr(time,'monotonic',lambda:clock[0])
    plan=stream_layout_call(c)
    if stage=='begin':
        with pytest.raises(_CompilerStreamEncoderRefused,match='stream_encoder_deadline'):encoder_start(c,plan,deadline=0)
        return
    encoder=encoder_start(c,plan,deadline=10)
    if stage=='after_encoding':
        original=type(encoder)._frame
        def expire(self):frame=original(self);clock[0]=10;return frame
        monkeypatch.setattr(type(encoder),'_frame',expire);action=encoder.next_frame
    elif stage=='next':clock[0]=10;action=encoder.next_frame
    else:
        frame=encoder.next_frame();encoder.accept_ack(encoder_ack(frame));frame=encoder.next_frame()
        if stage=='final_ack':encoder.mark_input_closed();action=lambda:encoder.accept_ack(encoder_ack(frame))
        else:action=encoder.mark_input_closed
        clock[0]=10
    with pytest.raises(_CompilerStreamEncoderRefused,match='stream_encoder_deadline'):action()
    assert encoder_snapshot(encoder,c)['state']=='ABORTED' and encoder._pending is None


def test_stream_encoder_actual_size_drift_is_terminal(stream_layout_context,monkeypatch):
    from tools.core import typescript_source_binding as binding
    c=stream_layout_context;encoder=encoder_start(c);original=binding._compiler_stream_frame_fields
    def changed(*args,**kwargs):body=original(*args,**kwargs);body['unexpected']='field';return body
    monkeypatch.setattr(binding,'_compiler_stream_frame_fields',changed)
    with pytest.raises(binding._CompilerStreamEncoderRefused,match='stream_encoder_frame_mismatch'):encoder.next_frame()
    assert encoder._capsule is encoder._pending is None


def test_stream_encoder_copies_metadata_and_refuses_serialization(stream_layout_context):
    import copy,pickle
    c=stream_layout_context;plan=stream_layout_call(c);encoder=encoder_start(c,plan)
    plan['manifest']['session_id']='changed';c['context']['session_id']='changed'
    c['profiles']['private_compiler_stream_encoder']['raw_transport_enabled']=True
    try:
        for call in (lambda:pickle.dumps(encoder),lambda:copy.copy(encoder),lambda:json.dumps(encoder)):
            with pytest.raises(TypeError):call()
        frames=encoder_drain(encoder,c)
        assert json.loads(frames[0])['session_id']=='stream-layout'
        result=encoder_snapshot(encoder,c);result['send_verified']=True
        assert encoder.snapshot()['send_verified'] is False and 'lib.d.ts' not in json.dumps(encoder.snapshot())
    finally:encoder.dispose();encoder.dispose()


@pytest.mark.parametrize('kind',['promoted','pending_without_hash','ready_with_hash','aborted_without_code'])
def test_stream_encoder_snapshot_schema_rejects_inconsistent_state(stream_layout_context,kind):
    from tools.core.artifact_validator import _validate
    c=stream_layout_context;encoder=encoder_start(c);result=encoder.snapshot()
    try:
        if kind=='promoted':result['send_verified']=True
        elif kind=='pending_without_hash':result['state']='AWAITING_ACK'
        elif kind=='ready_with_hash':result['outstanding_frame_sha256']='a'*64
        else:result['state']='ABORTED'
        errors=[];_validate(result,c['schema']['$defs']['encoder_snapshot'],c['schema'],['encoder_snapshot'],errors)
        assert errors
    finally:encoder.dispose()


@pytest.mark.parametrize('error',[RuntimeError,SystemExit])
def test_stream_encoder_unexpected_failure_cleans_without_masking(stream_layout_context,monkeypatch,error):
    c=stream_layout_context;encoder=encoder_start(c);failure=error('synthetic private failure')
    def fail(self):raise failure
    monkeypatch.setattr(type(encoder),'_frame',fail)
    with pytest.raises(error) as seen:encoder.next_frame()
    assert seen.value is failure
    assert encoder._capsule is encoder._held is encoder._pending is None
    assert encoder_snapshot(encoder,c)['state']=='ABORTED'
    assert c['capsule'].metadata()==c['expected_inventory']


def test_stream_encoder_begin_is_metadata_only_and_refuses_real_budget_result(stream_layout_context,monkeypatch):
    import base64
    from tools.core.typescript_source_binding import _CompilerStreamEncoderRefused
    c=stream_layout_context;plan=stream_layout_call(c)
    def forbidden(*args,**kwargs):raise AssertionError('begin encoded raw content')
    monkeypatch.setattr(base64,'b64encode',forbidden)
    with encoder_start(c,plan) as encoder:assert encoder_snapshot(encoder,c)['frames_encoded']==0
    profiles=deepcopy(c['profiles']);profiles['private_owned_config_handoff']['max_request_bytes']=1
    budget=stream_layout_call(c,profiles=profiles)
    assert budget['status']=='COMPILER_STREAM_LAYOUT_BUDGET_EXCEEDED_NOT_ADMITTED'
    with pytest.raises(_CompilerStreamEncoderRefused,match='stream_encoder_plan_invalid'):
        encoder_start(c,budget,profiles=profiles)


def test_stream_encoder_deadline_uses_held_policy_not_mutated_caller(stream_layout_context,monkeypatch):
    import time
    from tools.core.typescript_source_binding import _CompilerStreamEncoderRefused
    c=stream_layout_context;plan=stream_layout_call(c);original=json.loads;escaped=[]
    def copy_then_change(text,*args,**kwargs):
        result=original(text,*args,**kwargs)
        if isinstance(result,dict) and set(result)=={'expected_inventory','context','profiles','schema','plan'}:
            c['profiles']['private_input_image_experiment']['limits']['capture_ms']=1000000
        return result
    monkeypatch.setattr(json,'loads',copy_then_change)
    try:
        with pytest.raises(_CompilerStreamEncoderRefused,match='stream_encoder_deadline'):
            escaped.append(encoder_start(c,plan,deadline=time.monotonic()+50))
    finally:
        for encoder in escaped:encoder.dispose()


def stager_material(fixture):
    plan=stream_layout_call(fixture);manifest=plan['manifest']
    keys=('session_id','owner_context_sha256','context_sha256','inventory_sha256','policy_sha256','schema_sha256','compiler_source_sha256','compiler_version')
    expected={k:manifest[k] for k in keys};expected['manifest_sha256']=plan['manifest_sha256']
    return plan,actual_synthetic_stream_frames(plan,fixture['capsule'].library_bytes()),expected


def stager_begin(fixture,expected,**changes):
    import time
    from tools.core.typescript_source_binding import _begin_owned_compiler_stream_stager
    args={'profiles':fixture['profiles'],'schema':fixture['schema'],'deadline':time.monotonic()+20}
    args.update(changes)
    return _begin_owned_compiler_stream_stager(expected,**args)


def stager_snapshot(stager,fixture):
    from tools.core.artifact_validator import _validate
    result=stager.snapshot();errors=[]
    _validate(result,fixture['schema']['$defs']['stager_snapshot'],fixture['schema'],['stager_snapshot'],errors)
    assert not errors
    return result


def stager_seal(stager,frames,split=False):
    for frame in frames:
        cuts=(len(frame)//3,2*len(frame)//3) if split else ()
        pieces=[frame[:cuts[0]],frame[cuts[0]:cuts[1]],frame[cuts[1]:]] if cuts else [frame]
        for piece in pieces:
            ack=stager.feed(piece)
            if ack is not None:stager.release_credit(ack)
    assert stager.snapshot()['state']=='SEALED' and stager.snapshot()['raw_bytes_verified_in_model'] is False


def stager_wire(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)+'\n').encode()


@pytest.mark.parametrize('stream_layout_context',['small','zero','one','two','bom','multibyte','batch','large'],indirect=True)
@pytest.mark.parametrize('split',[False,True])
def test_stream_stager_independent_reference_frames_and_complete_borrow(stream_layout_context,split):
    c=stream_layout_context;plan,frames,expected=stager_material(c)
    with stager_begin(c,expected) as stager:
        stager_seal(stager,frames,split)
        final=stager.end_input();assert final==encoder_ack(frames[-1])
        result=stager_snapshot(stager,c)
        assert result['state']=='COMPLETE' and result['raw_bytes_verified_in_model'] is True
        assert result['wire_bytes_accepted']==plan['wire_bytes_total'] and result['raw_bytes_staged']==plan['library_raw_bytes']
        actual=stager.consume(lambda rows:{name:bytes(raw) for name,raw in rows})
        assert actual==c['capsule'].library_bytes()
        result=stager_snapshot(stager,c)
        assert result['state']=='DISPOSED' and stager._completed is stager._active is stager._held is None
        assert result['actual_transfer'] is result['raw_content_verified'] is result['host_text_verified'] is False
        assert result['whole_image_within_limit'] is result['peak_rss_bytes'] is None
    assert c['capsule'].metadata()==c['expected_inventory']


def test_stream_stager_composes_with_memory_encoder_without_driver(stream_layout_context):
    c=stream_layout_context;plan,_,expected=stager_material(c)
    with encoder_start(c,plan) as encoder,stager_begin(c,expected) as stager:
        while True:
            frame=encoder.next_frame();ack=stager.feed(frame)
            if json.loads(frame)['kind']=='commit':
                assert ack is None;encoder.mark_input_closed();encoder.accept_ack(stager.end_input());break
            encoder.accept_ack(ack);stager.release_credit(ack)
        assert encoder_snapshot(encoder,c)['state']=='COMPLETE'
        assert stager.consume(lambda rows:{name:bytes(raw) for name,raw in rows})==c['capsule'].library_bytes()


@pytest.mark.parametrize('key',['session_id','owner_context_sha256','context_sha256','inventory_sha256','manifest_sha256','compiler_source_sha256','compiler_version'])
def test_stream_stager_independent_pin_mismatch_before_raw_allocation(stream_layout_context,monkeypatch,key):
    from tools.core import typescript_source_binding as binding
    c=stream_layout_context;_,frames,expected=stager_material(c)
    expected[key]='other' if key in ('session_id','compiler_version') else 'b'*64
    stager=stager_begin(c,expected);allocations=[];original=bytearray
    def observe(size=0):allocations.append(size);return original(size)
    monkeypatch.setattr(binding,'bytearray',observe,raising=False)
    with pytest.raises(binding._CompilerStreamStagerRefused):stager.feed(frames[0])
    assert not allocations and stager._completed is stager._active is stager._held is None
    assert stager_snapshot(stager,c)['state']=='ABORTED'


@pytest.mark.parametrize('kind',['promoted','schema','version','extra_expected','schema_ref','policy_ref','bool_limit','deadline_nan','deadline_far'])
def test_stream_stager_refuses_untrusted_constructor_metadata(stream_layout_context,kind):
    from tools.core.typescript_source_binding import _CompilerStreamStagerRefused
    c=stream_layout_context;_,_,expected=stager_material(c);changes={}
    if kind in ('promoted','version','bool_limit'):
        profiles=deepcopy(c['profiles']);changes['profiles']=profiles
        if kind=='promoted':profiles['private_compiler_stream_stager']['raw_transport_enabled']=True
        elif kind=='version':profiles['private_compiler_stream_stager']['version']='future'
        else:profiles['private_input_image_experiment']['limits']['compiler_files']=True
    elif kind=='schema':changes['schema']=deepcopy(c['schema']);changes['schema']['title']+=' changed'
    elif kind=='extra_expected':expected['raw']='forbidden'
    elif kind=='schema_ref':expected['schema_sha256']='b'*64
    elif kind=='policy_ref':expected['policy_sha256']='b'*64
    else:changes['deadline']=float('nan') if kind=='deadline_nan' else 1e100
    with pytest.raises(_CompilerStreamStagerRefused,match='^stream_stager_'):stager_begin(c,expected,**changes)


@pytest.mark.parametrize('kind',['duplicate_key','space','crlf','bom','bool_seq','wrong_version','extra_field','nonfinite','invalid_utf8','not_object'])
def test_stream_stager_refuses_noncanonical_or_wrong_frame(stream_layout_context,kind):
    from tools.core.typescript_source_binding import _CompilerStreamStagerRefused
    c=stream_layout_context;_,frames,expected=stager_material(c);frame=frames[0];body=json.loads(frame)
    if kind=='duplicate_key':frame=b'{"seq":0,'+frame[1:]
    elif kind=='space':frame=b' '+frame
    elif kind=='crlf':frame=frame[:-1]+b'\r\n'
    elif kind=='bom':frame=b'\xef\xbb\xbf'+frame
    elif kind=='invalid_utf8':frame=b'\xff'+frame
    elif kind=='nonfinite':frame=b'{"seq":NaN}\n'
    elif kind=='not_object':frame=b'[]\n'
    else:
        if kind=='bool_seq':body['seq']=False
        elif kind=='wrong_version':body['version']='future'
        else:body['raw']='unexpected'
        frame=stager_wire(body)
    stager=stager_begin(c,expected)
    with pytest.raises(_CompilerStreamStagerRefused):stager.feed(frame)
    assert stager_snapshot(stager,c)['state']=='ABORTED' and stager._completed is None


@pytest.mark.parametrize('kind',['file_budget','count_budget','aggregate_budget','recipe_hole','recipe_index','recipe_seq','recipe_size','empty_hash','folded_names'])
def test_stream_stager_reservation_and_recipe_before_allocation(stream_layout_context,monkeypatch,kind):
    from tools.core import typescript_source_binding as binding
    c=stream_layout_context;_,frames,expected=stager_material(c);body=json.loads(frames[0]);manifest=body['manifest'];profiles=deepcopy(c['profiles'])
    if kind=='file_budget':profiles['private_input_image_experiment']['limits']['compiler_file_bytes']=1
    elif kind=='count_budget':
        manifest['files'].append({**manifest['files'][0],'name':'lib.z.d.ts','bytes':0,'raw_sha256':hashlib.sha256(b'').hexdigest()})
        profiles['private_input_image_experiment']['limits']['compiler_files']=1
    elif kind=='aggregate_budget':profiles['private_input_image_experiment']['limits']['compiler_bytes']=1
    elif kind=='recipe_hole':manifest['layout'][0]['records'][0]['offset']=1
    elif kind=='recipe_index':manifest['layout'][0]['records'][0]['file_index']=1
    elif kind=='recipe_seq':manifest['layout'][0]['seq']=2
    elif kind=='recipe_size':manifest['layout'][0]['jsonl_bytes']+=1
    elif kind=='empty_hash':manifest['files'][0]['bytes']=0
    else:
        for name in ('lib.A.d.ts','lib.a.d.ts'):manifest['files'].append({**manifest['files'][0],'name':name,'bytes':0,'raw_sha256':hashlib.sha256(b'').hexdigest()})
        manifest['files'].sort(key=lambda row:row['name'])
    expected['manifest_sha256']=binding._semantic_metadata_sha(manifest);body['manifest_sha256']=expected['manifest_sha256']
    stager=stager_begin(c,expected,profiles=profiles);allocations=[];original=bytearray
    def observe(size=0):allocations.append(size);return original(size)
    monkeypatch.setattr(binding,'bytearray',observe,raising=False)
    with pytest.raises(binding._CompilerStreamStagerRefused):stager.feed(stager_wire(body))
    assert not allocations and stager._completed is stager._active is None


@pytest.mark.parametrize('kind',['offset','index','length','chunk_hash','base64','padding','record_extra','record_missing','bool_index','header_seq'])
def test_stream_stager_record_or_chunk_corruption_aborts(stream_layout_context,kind):
    from tools.core.typescript_source_binding import _CompilerStreamStagerRefused
    c=stream_layout_context;_,frames,expected=stager_material(c);stager=stager_begin(c,expected)
    ack=stager.feed(frames[0]);stager.release_credit(ack);body=json.loads(frames[1]);row=body['records'][0]
    if kind=='offset':row['offset']=1
    elif kind=='index':row['file_index']=1
    elif kind=='length':row['raw_bytes']+=1
    elif kind=='chunk_hash':row['chunk_sha256']='b'*64
    elif kind=='base64':row['utf8Base64']='!'+row['utf8Base64'][1:]
    elif kind=='padding':row['utf8Base64']=row['utf8Base64'][:-1]+'A'
    elif kind=='record_extra':row['raw']='unexpected'
    elif kind=='record_missing':row.pop('offset')
    elif kind=='bool_index':row['file_index']=False
    else:body['seq']+=1
    with pytest.raises(_CompilerStreamStagerRefused):stager.feed(stager_wire(body))
    assert stager_snapshot(stager,c)['state']=='ABORTED' and stager._active is stager._completed is None


@pytest.mark.parametrize('kind',['whole_hash','commit_hash','commit_count','commit_total_bool','duplicate_commit','trailing_byte'])
def test_stream_stager_whole_raw_commit_and_trailing_checks(stream_layout_context,kind):
    from tools.core.typescript_source_binding import _CompilerStreamStagerRefused,_semantic_metadata_sha
    c=stream_layout_context;_,frames,expected=stager_material(c)
    if kind=='whole_hash':
        opened=json.loads(frames[0]);opened['manifest']['files'][0]['raw_sha256']='b'*64
        expected['manifest_sha256']=_semantic_metadata_sha(opened['manifest']);opened['manifest_sha256']=expected['manifest_sha256']
        frames[0]=stager_wire(opened);data=json.loads(frames[1]);data['manifest_sha256']=expected['manifest_sha256'];frames[1]=stager_wire(data)
    stager=stager_begin(c,expected)
    if kind=='whole_hash':
        ack=stager.feed(frames[0]);stager.release_credit(ack);action=lambda:stager.feed(frames[1])
    else:
        for frame in frames[:-1]:ack=stager.feed(frame);stager.release_credit(ack)
        if kind in ('duplicate_commit','trailing_byte'):
            assert stager.feed(frames[-1]) is None;action=lambda:stager.feed(frames[-1] if kind=='duplicate_commit' else b'x')
        else:
            body=json.loads(frames[-1])
            if kind=='commit_hash':body['transcript_sha256']='b'*64
            elif kind=='commit_count':body['files_complete']+=1
            else:body['raw_bytes_total']=False
            action=lambda:stager.feed(stager_wire(body))
    with pytest.raises(_CompilerStreamStagerRefused):action()
    assert stager_snapshot(stager,c)['raw_bytes_verified_in_model'] is False and stager._completed is None


@pytest.mark.parametrize('stage',['new','partial','open','data','before_eof_borrow','unreleased_credit','coalesced','frame_budget'])
def test_stream_stager_incomplete_credit_and_no_early_borrow(stream_layout_context,stage):
    from tools.core.typescript_source_binding import _CompilerStreamStagerRefused
    c=stream_layout_context;_,frames,expected=stager_material(c);stager=stager_begin(c,expected);calls=[]
    action=stager.end_input
    if stage=='partial':stager.feed(frames[0][:10])
    elif stage=='open':ack=stager.feed(frames[0]);stager.release_credit(ack)
    elif stage=='data':
        for frame in frames[:-1]:ack=stager.feed(frame);stager.release_credit(ack)
    elif stage=='before_eof_borrow':stager_seal(stager,frames);action=lambda:stager.consume(lambda rows:calls.append(rows))
    elif stage=='unreleased_credit':stager.feed(frames[0]);action=lambda:stager.feed(frames[1])
    elif stage=='coalesced':action=lambda:stager.feed(frames[0]+frames[1])
    elif stage=='frame_budget':action=lambda:stager.feed(b'x'*(c['profiles']['private_owned_config_handoff']['max_request_bytes']+1))
    with pytest.raises(_CompilerStreamStagerRefused):action()
    assert not calls and stager_snapshot(stager,c)['state']=='ABORTED' and stager._incoming is stager._completed is None


@pytest.mark.parametrize('kind',['wrong_hash','bool_seq','extra','missing_credit'])
def test_stream_stager_explicit_ack_delivery_is_bound(stream_layout_context,kind):
    from tools.core.typescript_source_binding import _CompilerStreamStagerRefused
    c=stream_layout_context;_,frames,expected=stager_material(c);stager=stager_begin(c,expected)
    ack=stager.feed(frames[0])
    if kind=='wrong_hash':ack['frame_sha256']='b'*64
    elif kind=='bool_seq':ack['seq']=False
    elif kind=='extra':ack['raw']='forbidden'
    else:stager.release_credit(ack)
    with pytest.raises(_CompilerStreamStagerRefused):stager.release_credit(ack)
    assert stager_snapshot(stager,c)['state']=='ABORTED'


@pytest.mark.parametrize('error',[MemoryError,KeyboardInterrupt,RuntimeError,SystemExit])
@pytest.mark.parametrize('stage',['frame','consumer'])
def test_stream_stager_all_failure_exits_clean_without_masking(stream_layout_context,monkeypatch,error,stage):
    c=stream_layout_context;_,frames,expected=stager_material(c);stager=stager_begin(c,expected);failure=error('private synthetic failure')
    if stage=='frame':
        def fail(self,frame):raise failure
        monkeypatch.setattr(type(stager),'_accept_frame',fail);action=lambda:stager.feed(frames[0])
    else:
        stager_seal(stager,frames);stager.end_input()
        def fail(rows):raise failure
        action=lambda:stager.consume(fail)
    with pytest.raises(error) as seen:action()
    assert seen.value is failure and stager._held is stager._active is stager._completed is stager._incoming is None
    assert stager_snapshot(stager,c)['state']=='ABORTED'


@pytest.mark.parametrize('stage',['begin','partial','release','eof','consume','after_frame','after_consumer'])
@pytest.mark.parametrize('stream_layout_context',['zero'],indirect=True)
def test_stream_stager_fixed_deadline_on_all_operations(stream_layout_context,monkeypatch,stage):
    import time
    from tools.core.typescript_source_binding import _CompilerStreamStagerRefused
    c=stream_layout_context;clock=[0.0];monkeypatch.setattr(time,'monotonic',lambda:clock[0]);_,frames,expected=stager_material(c)
    if stage=='begin':
        with pytest.raises(_CompilerStreamStagerRefused):stager_begin(c,expected,deadline=0)
        return
    stager=stager_begin(c,expected,deadline=10)
    if stage=='partial':stager.feed(frames[0][:10]);action=lambda:stager.feed(frames[0][10:])
    elif stage=='release':ack=stager.feed(frames[0]);action=lambda:stager.release_credit(ack)
    elif stage=='after_frame':
        original=type(stager)._accept_frame
        def expire(self,frame):result=original(self,frame);clock[0]=10;return result
        monkeypatch.setattr(type(stager),'_accept_frame',expire);action=lambda:stager.feed(frames[0])
    else:
        stager_seal(stager,frames)
        if stage=='eof':action=stager.end_input
        else:
            stager.end_input()
            def consume(rows):
                if stage=='after_consumer':clock[0]=10
            action=lambda:stager.consume(consume)
    if stage not in ('after_frame','after_consumer'):clock[0]=10
    with pytest.raises(_CompilerStreamStagerRefused,match='stream_stager_deadline'):action()
    assert stager_snapshot(stager,c)['state']=='ABORTED' and stager._completed is None


def test_stream_stager_no_io_no_sender_owner_or_early_allocation(stream_layout_context,monkeypatch):
    import builtins
    from tools.core import typescript_source_binding as binding
    c=stream_layout_context;_,frames,expected=stager_material(c)
    def forbidden(*args,**kwargs):raise AssertionError('stager used filesystem/capture/sender owner')
    with monkeypatch.context() as guard:
        for owner,name in [(Path,'open'),(Path,'resolve'),(builtins,'open'),(os,'scandir'),(binding,'_capture_owned_compiler_library_inputs'),
            (binding,'_compiler_stream_encoder_plan'),(type(c['capsule']),'metadata'),(type(c['capsule']),'library_bytes'),(type(c['capsule']),'dispose')]:guard.setattr(owner,name,forbidden)
        with stager_begin(c,expected) as stager:
            stager_seal(stager,frames);stager.end_input();actual=stager.consume(lambda rows:{name:bytes(raw) for name,raw in rows})
    assert actual==c['capsule'].library_bytes()


def test_stream_stager_metadata_copy_serialization_and_readonly_borrow(stream_layout_context):
    import copy,pickle
    c=stream_layout_context;_,frames,expected=stager_material(c);profiles=deepcopy(c['profiles']);stager=stager_begin(c,expected,profiles=profiles)
    expected['session_id']='changed';profiles['private_input_image_experiment']['limits']['capture_ms']=1
    try:
        for call in (lambda:pickle.dumps(stager),lambda:copy.copy(stager),lambda:json.dumps(stager)):
            with pytest.raises(TypeError):call()
        stager_seal(stager,frames);stager.end_input();views=stager.consume(lambda rows:tuple(raw for _,raw in rows))
        assert all(view.readonly for view in views) and bytes(views[0])==c['capsule'].library_bytes()['lib.d.ts']
        # Returned views can retain underlying bytes; dispose is not revocation.
        assert stager._completed is None and 'lib.d.ts' not in json.dumps(stager_snapshot(stager,c))
    finally:stager.dispose();stager.dispose()


@pytest.mark.parametrize('kind',['promoted','early_raw','complete_not_verified','aborted_no_code'])
def test_stream_stager_snapshot_schema_preserves_model_boundary(stream_layout_context,kind):
    from tools.core.artifact_validator import _validate
    c=stream_layout_context;_,_,expected=stager_material(c);stager=stager_begin(c,expected);result=stager.snapshot()
    try:
        if kind=='promoted':result['raw_content_verified']=True
        elif kind=='early_raw':result['raw_bytes_verified_in_model']=True
        elif kind=='complete_not_verified':result['state']='COMPLETE'
        else:result['state']='ABORTED'
        errors=[];_validate(result,c['schema']['$defs']['stager_snapshot'],c['schema'],['stager_snapshot'],errors);assert errors
    finally:stager.dispose()


@pytest.mark.parametrize('stream_layout_context',['one','two'],indirect=True)
def test_stream_stager_rejects_noncanonical_padding_bits_with_identical_raw(stream_layout_context):
    import base64
    from tools.core.typescript_source_binding import _CompilerStreamStagerRefused
    c=stream_layout_context;_,frames,expected=stager_material(c);body=json.loads(frames[1]);row=body['records'][0]
    alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
    encoded=row['utf8Base64'];index=len(encoded.rstrip('='))-1
    changed=encoded[:index]+alphabet[alphabet.index(encoded[index])+1]+encoded[index+1:]
    assert changed!=encoded and base64.b64decode(changed,validate=True)==base64.b64decode(encoded,validate=True)
    row['utf8Base64']=changed;stager=stager_begin(c,expected)
    ack=stager.feed(frames[0]);stager.release_credit(ack)
    with pytest.raises(_CompilerStreamStagerRefused,match='stream_stager_raw_identity'):stager.feed(stager_wire(body))
    assert stager_snapshot(stager,c)['state']=='ABORTED' and stager._completed is None


def test_stream_stager_consumer_value_error_preserves_identity(stream_layout_context):
    c=stream_layout_context;_,frames,expected=stager_material(c);stager=stager_begin(c,expected)
    stager_seal(stager,frames);stager.end_input();failure=ValueError('synthetic consumer refusal')
    def consume(rows):raise failure
    with pytest.raises(ValueError) as seen:stager.consume(consume)
    assert seen.value is failure and stager._held is stager._completed is None
    assert stager_snapshot(stager,c)['state']=='ABORTED'


@pytest.fixture
def stream_js_context(installed_compiler,monkeypatch,request):
    """Reuse finite capture under a no-process guard, then allow only the Node test adapter."""
    with monkeypatch.context() as guard:
        original=stream_layout_context.__wrapped__(installed_compiler,guard,request)
        context=next(original)
    if context['case']=='unicode':
        metadata=context['capsule'].metadata();metadata['compiler_version']='0.0.0-synthetic-😀\u2028\u2029'
        context['capsule']._metadata_json=json.dumps(metadata);context['expected_inventory']=metadata
    context['node_request']=request
    try:yield context
    finally:
        try:next(original)
        except StopIteration:pass


def stream_js_run(context,expected,steps,**changes):
    import shutil
    from tools.core.artifact_validator import _validate
    node=shutil.which('node');assert node
    payload={'expected':expected,'profiles':context['profiles'],'schema':context['schema'],'steps':steps};payload.update(changes)
    request=context['node_request'];request.node.user_properties.append(('synthetic_node_test_invocations',1))
    result=subprocess.run([node,str(CODE_MAPS_DIR/'tools/tests/fixtures/compiler_stream_stager_js_harness.cjs')],
        input=json.dumps(payload,ensure_ascii=True,allow_nan=False),capture_output=True,text=True,encoding='utf-8',
        cwd=CODE_MAPS_DIR,env={**os.environ,'NODE_OPTIONS':'','NODE_PATH':''},
        timeout=context['profiles']['private_input_image_experiment']['limits']['capture_ms']/1000)
    assert result.returncode==0,result.stderr[-1000:]
    out=json.loads(result.stdout);assert out['forbidden_attempts']==out['compiler_loads']==0
    request.node.user_properties.extend([('node_version',out['node_version']),('forbidden_attempts',out['forbidden_attempts']),('compiler_loads',out['compiler_loads'])])
    for row in [event['snapshot'] for event in out['events'] if 'snapshot' in event]+([out['final_snapshot']] if out['final_snapshot'] else []):
        errors=[];_validate(row,context['schema']['$defs']['js_stager_snapshot'],context['schema'],['js_stager_snapshot'],errors);assert not errors
    if out['final_snapshot']:assert out['final_snapshot']['owned_buffer_bytes']==0
    return out


def stream_js_feed(frame,**extra):
    import base64
    return {'op':'feed','bytes':base64.b64encode(frame).decode('ascii'),**extra}


def stream_js_steps(frames,split=False,consume=True):
    steps=[]
    for frame in frames:
        parts=[frame[:len(frame)//3],frame[len(frame)//3:2*len(frame)//3],frame[2*len(frame)//3:]] if split else [frame]
        steps.extend(stream_js_feed(part) for part in parts)
        if json.loads(frame)['kind']!='commit':steps.append({'op':'release'})
    steps.append({'op':'eof'})
    if consume:steps.append({'op':'consume'})
    return steps


def stream_js_shared_snapshot(row):
    return {key:value for key,value in row.items() if key not in ('version','status','owned_buffer_bytes')}


@pytest.mark.parametrize('stream_js_context',['small','zero','one','two','bom','multibyte','batch','large','unicode'],indirect=True)
@pytest.mark.parametrize('split',[False,True])
def test_stream_js_stager_python_wire_and_shared_states(stream_js_context,split):
    c=stream_js_context;plan,frames,expected=stager_material(c);steps=stream_js_steps(frames,split)
    out=stream_js_run(c,expected,steps);assert out['constructed'] and not any('error' in event for event in out['events'])
    with stager_begin(c,expected) as python:
        last_ack=None
        for step,event in zip(steps,out['events']):
            if step['op']=='feed':
                import base64
                value=python.feed(base64.b64decode(step['bytes']));last_ack=value or last_ack
            elif step['op']=='release':value=python.release_credit(last_ack)
            elif step['op']=='eof':value=python.end_input();last_ack=value
            else:value=python.consume(lambda rows:[{'name':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in rows])
            assert event['value']==value
            assert stream_js_shared_snapshot(event['snapshot'])==stream_js_shared_snapshot(stager_snapshot(python,c))
    expected_rows=[{'name':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in sorted(c['capsule'].library_bytes().items())]
    assert out['consumer_calls']==1 and out['consumer_result']==expected_rows
    assert out['events'][-1]['snapshot']['wire_bytes_accepted']==plan['wire_bytes_total']


@pytest.mark.parametrize('key',['session_id','owner_context_sha256','context_sha256','inventory_sha256','manifest_sha256','compiler_source_sha256','compiler_version'])
def test_stream_js_stager_independent_pins_refuse_open(stream_js_context,key):
    c=stream_js_context;_,frames,expected=stager_material(c)
    expected[key]='other' if key in ('session_id','compiler_version') else 'b'*64
    out=stream_js_run(c,expected,[stream_js_feed(frames[0])])
    assert out['constructed'] and out['events'][-1]['error'].startswith('stream_stager_') and out['consumer_calls']==0
    assert out['events'][-1]['snapshot']['raw_bytes_staged']==0 and out['events'][-1]['snapshot']['state']=='ABORTED'
    with stager_begin(c,expected) as python:
        from tools.core.typescript_source_binding import _CompilerStreamStagerRefused
        with pytest.raises(_CompilerStreamStagerRefused):python.feed(frames[0])


@pytest.mark.parametrize('kind',['missing_js_policy','promoted','version','extra_expected','bad_schema_ref','bad_policy_ref','bool_limit','far_deadline','schema_drift','unsupported_assertion','ref_sibling'])
def test_stream_js_stager_constructor_and_schema_fail_closed(stream_js_context,kind):
    from tools.core.typescript_source_binding import _semantic_metadata_sha
    c=stream_js_context;_,_,expected=stager_material(c);profiles=deepcopy(c['profiles']);schema=deepcopy(c['schema']);extra={}
    if kind=='missing_js_policy':profiles.pop('private_compiler_stream_js_stager')
    elif kind=='promoted':profiles['private_compiler_stream_js_stager']['raw_transport_enabled']=True
    elif kind=='version':profiles['private_compiler_stream_js_stager']['version']='future'
    elif kind=='extra_expected':expected['raw']='forbidden'
    elif kind=='bad_schema_ref':expected['schema_sha256']='b'*64
    elif kind=='bad_policy_ref':expected['policy_sha256']='b'*64
    elif kind=='bool_limit':profiles['private_input_image_experiment']['limits']['compiler_files']=True
    elif kind=='far_deadline':extra['deadline']=1000000
    elif kind=='schema_drift':schema['title']+=' drift'
    else:
        if kind=='unsupported_assertion':schema['$defs']['stager_expected']['anyOf']=[{'type':'object'}]
        else:schema['$defs']['js_stager_policy']['properties']['schema_sha256']={'$ref':'#/$defs/stream_range','type':'string'}
        digest=_semantic_metadata_sha(schema)
        for key in ('private_compiler_transport_plan','private_compiler_stream_layout','private_compiler_stream_encoder','private_compiler_stream_stager','private_compiler_stream_js_stager'):profiles[key]['schema_sha256']=digest
        expected['schema_sha256']=digest;expected['policy_sha256']=_semantic_metadata_sha(profiles['private_compiler_stream_layout'])
    out=stream_js_run(c,expected,[],profiles=profiles,schema=schema,**extra)
    assert not out['constructed'] and out['constructor_error'].startswith('stream_stager_')


@pytest.mark.parametrize('kind',['duplicate_key','space','crlf','bom','bool_seq','wrong_version','extra_field','proto_field','unsafe_seq','nonfinite','invalid_utf8','lone_surrogate','not_object'])
def test_stream_js_stager_wrong_or_noncanonical_frame(stream_js_context,kind):
    c=stream_js_context;_,frames,expected=stager_material(c);frame=frames[0];body=json.loads(frame)
    if kind=='duplicate_key':frame=b'{"seq":0,'+frame[1:]
    elif kind=='space':frame=b' '+frame
    elif kind=='crlf':frame=frame[:-1]+b'\r\n'
    elif kind=='bom':frame=b'\xef\xbb\xbf'+frame
    elif kind=='invalid_utf8':frame=b'\xff'+frame
    elif kind=='nonfinite':frame=b'{"seq":NaN}\n'
    elif kind=='not_object':frame=b'[]\n'
    elif kind=='lone_surrogate':frame=b'{"seq":0,"value":"\\ud800"}\n'
    else:
        if kind=='bool_seq':body['seq']=False
        elif kind=='wrong_version':body['version']='future'
        elif kind=='unsafe_seq':body['seq']=2**53
        elif kind=='proto_field':body['__proto__']={'polluted':True}
        else:body['raw']='unexpected'
        frame=stager_wire(body)
    out=stream_js_run(c,expected,[stream_js_feed(frame)])
    assert out['events'][-1]['error'].startswith('stream_stager_') and out['consumer_calls']==0
    from tools.core.typescript_source_binding import _CompilerStreamStagerRefused
    with stager_begin(c,expected) as python:
        with pytest.raises(_CompilerStreamStagerRefused):python.feed(frame)


@pytest.mark.parametrize('kind',['file_budget','count_budget','aggregate_budget','recipe_hole','recipe_index','recipe_seq','recipe_size','empty_hash','folded_names'])
def test_stream_js_stager_reservation_and_recipe_refusals(stream_js_context,kind):
    from tools.core.typescript_source_binding import _semantic_metadata_sha
    c=stream_js_context;_,frames,expected=stager_material(c);body=json.loads(frames[0]);manifest=body['manifest'];profiles=deepcopy(c['profiles'])
    if kind=='file_budget':profiles['private_input_image_experiment']['limits']['compiler_file_bytes']=1
    elif kind=='count_budget':
        manifest['files'].append({**manifest['files'][0],'name':'lib.z.d.ts','bytes':0,'raw_sha256':hashlib.sha256(b'').hexdigest()})
        profiles['private_input_image_experiment']['limits']['compiler_files']=1
    elif kind=='aggregate_budget':profiles['private_input_image_experiment']['limits']['compiler_bytes']=1
    elif kind=='recipe_hole':manifest['layout'][0]['records'][0]['offset']=1
    elif kind=='recipe_index':manifest['layout'][0]['records'][0]['file_index']=1
    elif kind=='recipe_seq':manifest['layout'][0]['seq']=2
    elif kind=='recipe_size':manifest['layout'][0]['jsonl_bytes']=profiles['private_owned_config_handoff']['max_request_bytes']+1
    elif kind=='empty_hash':manifest['files'][0]['bytes']=0
    else:
        for name in ('lib.A.d.ts','lib.a.d.ts'):manifest['files'].append({**manifest['files'][0],'name':name,'bytes':0,'raw_sha256':hashlib.sha256(b'').hexdigest()})
        manifest['files'].sort(key=lambda row:row['name'])
    expected['manifest_sha256']=_semantic_metadata_sha(manifest);body['manifest_sha256']=expected['manifest_sha256']
    out=stream_js_run(c,expected,[stream_js_feed(stager_wire(body))],profiles=profiles)
    assert out['events'][-1]['error'].startswith('stream_stager_') and out['events'][-1]['snapshot']['raw_bytes_staged']==0 and out['consumer_calls']==0


@pytest.mark.parametrize('kind',['offset','index','length','chunk_hash','base64','padding','extra','missing','bool_index','header_seq'])
def test_stream_js_stager_record_and_chunk_corruption(stream_js_context,kind):
    c=stream_js_context;_,frames,expected=stager_material(c);body=json.loads(frames[1]);row=body['records'][0]
    if kind=='offset':row['offset']=1
    elif kind=='index':row['file_index']=1
    elif kind=='length':row['raw_bytes']+=1
    elif kind=='chunk_hash':row['chunk_sha256']='b'*64
    elif kind=='base64':row['utf8Base64']='!'+row['utf8Base64'][1:]
    elif kind=='padding':row['utf8Base64']=row['utf8Base64'][:-1]+'A'
    elif kind=='extra':row['raw']='unexpected'
    elif kind=='missing':row.pop('offset')
    elif kind=='bool_index':row['file_index']=False
    else:body['seq']+=1
    out=stream_js_run(c,expected,[stream_js_feed(frames[0]),{'op':'release'},stream_js_feed(stager_wire(body))])
    assert out['events'][-1]['error'].startswith('stream_stager_') and out['events'][-1]['snapshot']['state']=='ABORTED'


@pytest.mark.parametrize('stream_js_context',['one','two'],indirect=True)
def test_stream_js_stager_rejects_noncanonical_padding_with_same_raw(stream_js_context):
    import base64
    c=stream_js_context;_,frames,expected=stager_material(c);body=json.loads(frames[1]);row=body['records'][0]
    alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
    encoded=row['utf8Base64'];index=len(encoded.rstrip('='))-1
    changed=encoded[:index]+alphabet[alphabet.index(encoded[index])+1]+encoded[index+1:]
    assert base64.b64decode(changed,validate=True)==base64.b64decode(encoded,validate=True)
    row['utf8Base64']=changed
    out=stream_js_run(c,expected,[stream_js_feed(frames[0]),{'op':'release'},stream_js_feed(stager_wire(body))])
    assert out['events'][-1]['error']=='stream_stager_raw_identity'


@pytest.mark.parametrize('kind',['new_eof','partial_eof','missing_commit','early_consume','unreleased_credit','coalesced','frame_budget','duplicate_commit','trailing_byte','eof_twice'])
def test_stream_js_stager_credit_incomplete_and_trailing_input(stream_js_context,kind):
    c=stream_js_context;_,frames,expected=stager_material(c)
    if kind=='new_eof':steps=[{'op':'eof'}]
    elif kind=='partial_eof':steps=[stream_js_feed(frames[0][:10]),{'op':'eof'}]
    elif kind=='missing_commit':steps=[stream_js_feed(frames[0]),{'op':'release'},stream_js_feed(frames[1]),{'op':'release'},{'op':'eof'}]
    elif kind=='early_consume':steps=stream_js_steps(frames,consume=False)[:-1]+[{'op':'consume'}]
    elif kind=='unreleased_credit':steps=[stream_js_feed(frames[0]),stream_js_feed(frames[1])]
    elif kind=='coalesced':steps=[stream_js_feed(frames[0]+frames[1])]
    elif kind=='frame_budget':steps=[stream_js_feed(b'x'*(c['profiles']['private_owned_config_handoff']['max_request_bytes']+1))]
    elif kind=='eof_twice':steps=stream_js_steps(frames,consume=False)+[{'op':'eof'}]
    else:steps=stream_js_steps(frames,consume=False)[:-1]+[stream_js_feed(frames[-1] if kind=='duplicate_commit' else b'x')]
    out=stream_js_run(c,expected,steps)
    assert out['events'][-1]['error'].startswith('stream_stager_') and out['consumer_calls']==0
    assert out['events'][-1]['snapshot']['state']=='ABORTED'


@pytest.mark.parametrize('kind',['whole_hash','commit_hash','commit_count','commit_total_bool'])
def test_stream_js_stager_whole_hash_and_commit_checks(stream_js_context,kind):
    from tools.core.typescript_source_binding import _semantic_metadata_sha
    c=stream_js_context;_,frames,expected=stager_material(c)
    if kind=='whole_hash':
        body=json.loads(frames[0]);body['manifest']['files'][0]['raw_sha256']='b'*64
        expected['manifest_sha256']=_semantic_metadata_sha(body['manifest']);body['manifest_sha256']=expected['manifest_sha256'];frames[0]=stager_wire(body)
        body=json.loads(frames[1]);body['manifest_sha256']=expected['manifest_sha256'];frames[1]=stager_wire(body)
        steps=[stream_js_feed(frames[0]),{'op':'release'},stream_js_feed(frames[1])]
    else:
        body=json.loads(frames[-1])
        if kind=='commit_hash':body['transcript_sha256']='b'*64
        elif kind=='commit_count':body['files_complete']+=1
        else:body['raw_bytes_total']=False
        frames[-1]=stager_wire(body);steps=stream_js_steps(frames,consume=False)
    out=stream_js_run(c,expected,steps)
    assert out['events'][-1]['error'].startswith('stream_stager_') and out['consumer_calls']==0


@pytest.mark.parametrize('kind',['wrong_hash','bool_seq','extra','twice'])
def test_stream_js_stager_model_ack_delivery_is_pinned(stream_js_context,kind):
    c=stream_js_context;_,frames,expected=stager_material(c);ack=encoder_ack(frames[0]);steps=[stream_js_feed(frames[0])]
    if kind=='wrong_hash':ack['frame_sha256']='b'*64
    elif kind=='bool_seq':ack['seq']=False
    elif kind=='extra':ack['raw']='unexpected'
    else:steps.append({'op':'release'})
    steps.append({'op':'release','ack':ack})
    out=stream_js_run(c,expected,steps);assert out['events'][-1]['error']=='stream_stager_credit'


@pytest.mark.parametrize('stage',['partial','release','eof','consume','after_consumer','nan_clock'])
@pytest.mark.parametrize('stream_js_context',['zero'],indirect=True)
def test_stream_js_stager_deadline_covers_partial_and_terminal_paths(stream_js_context,stage):
    c=stream_js_context;_,frames,expected=stager_material(c);expire={'op':'clock','value':20000}
    if stage=='partial':steps=[stream_js_feed(frames[0][:10]),expire,stream_js_feed(frames[0][10:])]
    elif stage=='release':steps=[stream_js_feed(frames[0]),expire,{'op':'release'}]
    elif stage=='eof':steps=stream_js_steps(frames,consume=False)[:-1]+[expire,{'op':'eof'}]
    elif stage=='consume':steps=stream_js_steps(frames,consume=False)+[expire,{'op':'consume'}]
    elif stage=='nan_clock':steps=[{'op':'clock','value':'nan'},stream_js_feed(frames[0])]
    else:steps=stream_js_steps(frames,consume=False)+[{'op':'consume','control':'expire'}]
    out=stream_js_run(c,expected,steps);assert out['events'][-1]['error']=='stream_stager_deadline'
    assert out['events'][-1]['snapshot']['state']=='ABORTED'


@pytest.mark.parametrize('control',['allocation','throw','async','promise','retain','mutate','serialize'])
def test_stream_js_stager_failure_cleanup_and_borrow_lifetime(stream_js_context,control):
    c=stream_js_context;_,frames,expected=stager_material(c)
    if control=='allocation':steps=[stream_js_feed(frames[0],inject_allocation=True)]
    elif control=='serialize':steps=[{'op':'serialize'}]+stream_js_steps(frames)
    else:steps=stream_js_steps(frames,consume=False)+[{'op':'consume','control':control}]
    out=stream_js_run(c,expected,steps)
    if control in ('allocation','throw'):
        event=out['events'][-1];assert event['error']=='UNEXPECTED_ERROR' and event['original_error_preserved'] is True
        assert event['snapshot']['state']=='ABORTED' and event['snapshot']['owned_buffer_bytes']==0
    elif control in ('async','promise'):
        assert out['events'][-1]['error']=='stream_stager_order' and out['consumer_calls']==(0 if control=='async' else 1)
    else:
        assert not any('error' in event for event in out['events'])
        if control=='serialize':assert out['events'][0]['value']=={'json_refused':True,'clone_refused':True}
        elif control=='retain':assert out['retained_after_dispose']['sha256']==hashlib.sha256(c['capsule'].library_bytes()['lib.d.ts']).hexdigest()
        else:assert out['borrow_mutable'] is True


def test_stream_js_stager_wrong_cost_stops_at_actual_frame_before_raw_staging(stream_js_context):
    from tools.core.typescript_source_binding import _semantic_metadata_sha,_CompilerStreamStagerRefused
    c=stream_js_context;_,frames,expected=stager_material(c);opened=json.loads(frames[0]);data=json.loads(frames[1])
    opened['manifest']['layout'][0]['jsonl_bytes']+=1
    expected['manifest_sha256']=_semantic_metadata_sha(opened['manifest'])
    opened['manifest_sha256']=data['manifest_sha256']=expected['manifest_sha256']
    frames[0]=stager_wire(opened);frames[1]=stager_wire(data)
    # Python can use its existing counter and reject at open. JS deliberately
    # checks the pinned prediction against actual data, without copying that equation.
    with stager_begin(c,expected) as python:
        with pytest.raises(_CompilerStreamStagerRefused):python.feed(frames[0])
    out=stream_js_run(c,expected,[stream_js_feed(frames[0]),{'op':'release'},stream_js_feed(frames[1])])
    assert 'error' not in out['events'][0] and out['events'][0]['snapshot']['state']=='OPEN'
    assert out['events'][-1]['error']=='stream_stager_record_invalid' and out['consumer_calls']==0
    assert out['events'][-1]['snapshot']['raw_bytes_staged']==0 and out['events'][-1]['snapshot']['owned_buffer_bytes']==0


def test_stream_js_stager_owns_metadata_copy_through_completion(stream_js_context):
    c=stream_js_context;_,frames,expected=stager_material(c)
    out=stream_js_run(c,expected,[{'op':'caller_metadata'}]+stream_js_steps(frames))
    assert not any('error' in row for row in out['events']) and out['consumer_calls']==1
    assert out['consumer_result'][0]['sha256']==hashlib.sha256(c['capsule'].library_bytes()['lib.d.ts']).hexdigest()
    assert out['events'][-1]['snapshot']['actual_transfer'] is False

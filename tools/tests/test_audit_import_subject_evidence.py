"""Finite source/policy transport, not target execution or causal proof."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from tools.core import audit_finding_generation as evidence
from tools.core import contextos_mcp, layer_resolver
from tools.core.architecture_blueprints import build_effective_architecture_policy
from tools.core.artifact_store import ArtifactStore
from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION
from tools.core.atlas_integrity import build_atlas_commit
from tools.core.atlas_io import load_atlas_data_with_identity
from tools.core.audit_rules import build_effective_project_rule_taxonomy, get_audit_runtime_context
from tools.core.db import SQLiteManager
from tools.core.path_engine import resolve_project_import
from tools.core.polyglot_imports import extract_typescript_import_evidence
from tools.core.source_snapshot_reader import read_source_snapshot_binding
from tools.engines import audit
from tools.mcp import server

ROOT = Path(__file__).resolve().parents[2]
RULE = 'hexagonal_layer_violation'
CONTENT = """// İstanbul 🔎\nimport { View, Style } from '@/widgets/view';
import { View as OtherView } from '@/widgets/view';
import DefaultView from '@/widgets/default';
import * as Theme from '@/widgets/theme';
import '@/widgets/setup';
export const surface = { View, OtherView, DefaultView, Theme };
"""
ENTITY_CONTENT = """// Source record set, not React execution.
import { useState, useEffect } from 'react';
import { useState as other } from 'react';
import React from 'react';
import * as ReactNS from 'react';
import 'react';
export const result = { useState, useEffect, other, React, ReactNS };
"""
MODULE_CONTENT = """// İstanbul 🔎 finite module path facts, not export resolution.
import { View, Style } from '@/lifecycle-modules/b/private';
import { View as Other } from '@/lifecycle-modules/b/private';
import DefaultView from '@/lifecycle-modules/b/private';
import * as Theme from '@/lifecycle-modules/b/private';
import '@/lifecycle-modules/b/private';
import { Own } from '@/lifecycle-modules/a/api';
export const value = { View, Style, Other, DefaultView, Theme, Own };
"""
MULTI_MODULE_CONTENT = MODULE_CONTENT.replace('lifecycle-modules', 'features/catalog')


@pytest.fixture(scope='module')
def native_imports(tmp_path_factory):
    directory = tmp_path_factory.mktemp('audit-import-native')
    files = [directory / f'{name}.{ext}' for name in ['Panel', 'Entity', 'Module', 'MultiModule'] for ext in ['ts', 'js']]
    for file in files:
        file.write_bytes({'Panel': CONTENT, 'Entity': ENTITY_CONTENT, 'Module': MODULE_CONTENT,
            'MultiModule': MULTI_MODULE_CONTENT}[file.stem].encode('utf-8'))
    result = subprocess.run(['node', str(ROOT / 'tools/engines/ast_sequencer.cjs'),
        '--batch-json', *map(str, files)], capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    return {file.suffix if file.stem == 'Panel' else file.name:
        extract_typescript_import_evidence(parsed[str(file)]) for file in files}


@pytest.fixture
def fixture(tmp_path, native_imports, monkeypatch):
    return _fixture(tmp_path, native_imports['.ts'], monkeypatch)


def _fixture(tmp_path, native, monkeypatch, *, rel_path='domain/Panel.ts', content=CONTENT, profile='HEXAGONAL_PURE'):
    raw = tmp_path / '.raw'; raw.mkdir()
    root = tmp_path / 'repo'; source_root = root / 'src'
    (source_root / 'domain').mkdir(parents=True); (source_root / 'widgets').mkdir()
    source = source_root / rel_path; source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content.encode('utf-8'))
    for name in ['view', 'default', 'theme', 'setup']:
        (source_root / f'widgets/{name}.ts').write_text('export const View = 1;', encoding='utf-8')
    records = [{**r, 'raw_source': r['source'], 'source': resolve_project_import(r['source'],
        str(source.parent), str(root), str(source_root), alias_map={'@/': str(source_root)})}
        for r in native['records']]
    meta = {'language': 'javascript' if source.suffix == '.js' else 'typescript', 'hash': hashlib.sha256(content.encode()).hexdigest(),
        'size': source.stat().st_size, 'symbols': [], 'features': [], 'loc': len(content.splitlines()),
        'ast_contract_version': AST_CONTRACT_VERSION, 'parser_evidence': {'status': 'observed'},
        'workspace_rel': f'src/{rel_path}', 'repo_relative_path': f'src/{rel_path}',
        'import_records': records}
    atlas = {'MAIN': {'root_path': str(source_root), 'project_type': 'typescript',
        'files': {rel_path: meta}, 'dependencies': {}, 'symbols': []}}
    store = ArtifactStore(raw_dir=raw); store.use_sqlite = True; store.backend = 'hybrid_sqlite'
    store.db_manager = SQLiteManager(raw / 'codemaps.db'); store._schema_initialized = False; store._ensure_schema()
    store._save_payload_to_state_table('atlas', atlas); store._save_atlas_to_sqlite(atlas)
    commit = build_atlas_commit(atlas); store._save_payload_to_state_table('atlas_commit', commit)
    oracle = {'projects': [{'project': 'MAIN', 'proposal_identity': 'explicit-test-fixture-proposal',
        'proposal_identity_status': 'BOUND', 'classification_status': 'SOURCE_EVIDENCE',
        'recommended_profile': profile, 'project_language_capability': {'status': 'ENGINE_AVAILABLE'}}],
        'repository_blueprint': {'evidence': {'snapshot_binding': {'atlas_snapshot_id': commit['snapshot_id']}}}}
    approvals = {'entries': [{'id': 'explicit-test-fixture-approval-not-real-hitl', 'scope': 'MAIN',
        'gate': 'architecture_doctrine_seal', 'decision': 'approved',
        'evidence': ['architecture_proposal_identity:explicit-test-fixture-proposal']}]}
    policy = build_effective_architecture_policy(oracle, approvals)
    store._save_payload_to_state_table('effective_architecture_policy', policy)
    monkeypatch.setattr(audit, 'RAW_DIR', raw)
    monkeypatch.setattr(audit, 'resolve_runtime_projects', lambda *_: {'MAIN': source_root})
    monkeypatch.setattr(audit, 'watchdog_artifact_identity', lambda: {
        **audit._canonical_audit_artifact_identity(atlas), 'artifact_root': str(raw),
        'target_descriptor': {'target_root': str(root), 'fixture_scope_only': True}})
    monkeypatch.setattr(audit, 'watchdog_artifact_path', lambda *_: raw / 'watchdog_audit.json')
    monkeypatch.setattr(audit, '_load_structural_contract_health', lambda: {
        'status': 'not_available_in_audit_phase', 'reason': 'isolated source-policy fixture, no target quality verdict',
        **{k: 0.0 for k in ['atlas_contract_file_ratio', 'member_detail_contract_ratio',
            'genome_occurrence_contract_ratio', 'atlas_current_version_ratio', 'genome_current_version_ratio']}})
    output = []
    monkeypatch.setattr(audit, 'save_json_atomic', lambda _path, payload: output.append(deepcopy(payload)))
    assert audit.analyze_project(changed_files=[f'MAIN::{rel_path}'], atlas=atlas)
    report = output[-1]
    store._save_audit_findings_to_sqlite(report)
    rows = [row for row in report['violations'] if row['rule'] == RULE]
    if rel_path == 'domain/Panel.ts':
        assert rows, 'actual Audit did not emit the applicable path-model findings'
    return {'raw': raw, 'root': root, 'store': store, 'atlas': atlas, 'meta': meta, 'commit': commit,
        'policy': policy, 'source': source, 'report': report, 'rows': rows, 'rel_path': rel_path}


@pytest.fixture(params=['domain/Panel.ts', 'domain/Panel.js', 'entities/Entity.ts'])
def restriction_fixture(tmp_path, native_imports, monkeypatch, request):
    rel = request.param
    entity = rel.startswith('entities/')
    native = native_imports[Path(rel).name if entity else Path(rel).suffix]
    data = _fixture(tmp_path, native, monkeypatch, rel_path=rel, content=ENTITY_CONTENT if entity else CONTENT)
    rule = 'impure_entities' if entity else 'domain_ui_leaks'
    rows = [r for r in data['report']['violations'] if r['rule'] == rule]
    assert rows and all(r['finding_evidence']['version'] == 'v2' for r in rows)
    return {**data, 'restriction_rule': rule, 'restriction_rows': rows}


def project(fixture, row=None, **kwargs):
    return evidence.project_audit_import_subject(row or fixture['rows'][0], raw_dir=fixture['raw'],
        project='MAIN', rel_path='domain/Panel.ts', **kwargs)


def resign(row):
    row['finding_evidence']['binding_id'] = evidence._import_evidence_digest(
        {k: v for k, v in row['finding_evidence'].items() if k != 'binding_id'})


@pytest.fixture(params=['ts', 'js'])
def module_fixture(tmp_path, native_imports, monkeypatch, request):
    ext = request.param
    monkeypatch.setattr(audit, 'get_module_root_name', lambda: 'lifecycle-modules')
    data = _fixture(tmp_path, native_imports[f'Module.{ext}'], monkeypatch,
        rel_path=f'lifecycle-modules/a/Module.{ext}', content=MODULE_CONTENT, profile='FSD_STANDARD')
    rows = [r for r in data['report']['violations'] if r['rule'] in ['deep_imports', 'own_api_imports']]
    assert len(rows) == 2
    return {**data, 'module_rows': rows}


def test_module_v3_actual_scoped_pipeline_and_three_consumers(module_fixture):
    data = module_fixture
    items, total, _ = server._audit_violation_work_items_from_sqlite(data['raw'], page=1, page_size=20, project='MAIN')
    integrity, itotal, _ = server._module_integrity_items_from_sqlite(data['raw'], f"MAIN::{data['rel_path']}",
        max_items=20, target_root=str(data['root']))
    directives = contextos_mcp.build_agent_action_directives(audit_report={'violations': data['module_rows']},
        raw_dir=data['raw'], _atlas_read=load_atlas_data_with_identity(data['raw']), max_items=20)
    assert total == itotal == 2
    schema = json.loads((ROOT / 'config/schemas/audit_report.schema.json').read_text())
    from tools.core.artifact_validator import _validate
    import jsonschema
    for row in data['module_rows']:
        e = row['finding_evidence']
        assert e['version'] == 'v3' and e['status'] == 'bound_record_set'
        assert 'source_layer' not in e and 'target_layer' not in e
        assert e['policy']['runtime_context']['module_container_resolution'] == 'captured_runtime_value'
        errors = []; _validate(e, schema['definitions']['audit_import_module_subject'], schema, ['module'], errors)
        assert not errors
        jsonschema.Draft7Validator({**schema, '$ref': '#/definitions/audit_import_module_subject'}).validate(e)
        directive = next(x for x in directives if x['rule'] == row['rule'])
        for projected in [next(x for x in items if x['rule'] == row['rule']),
                          next(x for x in integrity if x['rule'] == row['rule']), directive]:
            assert projected['finding_evidence'] == e
        if row['rule'] == 'deep_imports':
            # Renaming the same imported View does not create a new record or
            # prove a distinct source occurrence in the current AST contract.
            assert len(e['subjects']) == 5 and e['module_trace']['api_entry_policy'] is None
        else:
            assert e['module_trace']['api_entry_policy']['owner'] == 'modular_doctrine'
            assert e['module_trace']['source_api_entry_exemption'] is False


@pytest.mark.parametrize('case', ['module', 'imported', 'prefix', 'offset_type', 'terminal_type',
    'branch', 'api_owner', 'runtime', 'extra', 'rule', 'configuration', 'alias_consistency'])
def test_module_v3_resealed_context_refused_not_suppressed(module_fixture, case):
    data = module_fixture
    row = deepcopy(next(r for r in data['module_rows'] if r['rule'] == ('own_api_imports' if case == 'api_owner' else 'deep_imports')))
    trace = row['finding_evidence']['module_trace']
    if case == 'module': trace['source_module'] = 'other'
    elif case == 'imported': trace['imported_module'] = 'other'
    elif case == 'prefix': trace['module_import_prefix'] = '~/wrong/'
    elif case == 'offset_type': trace['raw_prefix_offset'] = float(trace['raw_prefix_offset'])
    elif case == 'terminal_type': trace['raw_api_terminal'] = 0
    elif case == 'branch': trace['decision_branch'] = 'same_module_literal_api_path'
    elif case == 'api_owner': trace['api_entry_policy']['owner'] = 'other'
    elif case == 'runtime': row['finding_evidence']['runtime_proof'] = 'confirmed'
    elif case == 'extra': row['finding_evidence']['safe_to_apply'] = True
    elif case == 'rule': row['finding_evidence']['rule'] = 'own_api_imports'
    elif case == 'configuration':
        policy = row['finding_evidence']['policy']
        policy['runtime_context']['module_runtime_configuration']['primary_alias'] = '~/'
        policy['identity'] = evidence._import_evidence_digest({k: v for k, v in policy.items() if k != 'identity'})
    elif case == 'alias_consistency':
        policy = row['finding_evidence']['policy']
        policy['runtime_context']['primary_alias'] = ''
        trace.update(primary_alias='', module_import_prefix='lifecycle-modules/', raw_prefix_offset=2)
        policy['identity'] = evidence._import_evidence_digest({k: v for k, v in policy.items() if k != 'identity'})
    resign(row)
    assert evidence.project_audit_import_subject(row, raw_dir=data['raw'], project='MAIN', rel_path=data['rel_path'])['status'] == 'unavailable'
    report = deepcopy(data['report'])
    report['violations'] = [row if r['rule'] == row['rule'] else r for r in report['violations']]
    data['store']._save_audit_findings_to_sqlite(report)
    items, total, _ = server._audit_violation_work_items_from_sqlite(data['raw'], page=1, page_size=20, project='MAIN')
    assert total == 2
    current = next(x for x in items if x['rule'] == row['rule'])
    assert current['finding_evidence']['version'] == 'v3'
    assert current['finding_evidence']['status'] == 'unavailable'


def test_module_v3_projection_never_recalculates_live_container(module_fixture, monkeypatch):
    from tools.core import pipeline_policy, fractal_policy
    data = module_fixture
    fail = lambda: pytest.fail('projection rediscovered live topology')
    monkeypatch.setattr(pipeline_policy, 'get_module_root_name', fail)
    monkeypatch.setattr(fractal_policy, 'get_module_container', fail)
    rows, total, _ = server._audit_violation_work_items_from_sqlite(data['raw'], page=1, page_size=20, project='MAIN')
    assert total == 2 and all(x['finding_evidence']['status'] == 'bound_record_set' for x in rows)


@pytest.mark.parametrize('case', ['snapshot', 'policy', 'generation', 'configuration'])
def test_module_v3_current_primary_failures_keep_findings(module_fixture, monkeypatch, case):
    from tools.core import pipeline_policy
    data = module_fixture
    if case == 'snapshot':
        with data['store'].db_manager.transaction() as conn:
            conn.execute('DELETE FROM source_snapshots;')
        assert data['source'].exists(), 'tempting live source must not rescue snapshot evidence'
    elif case == 'policy':
        policy = deepcopy(data['policy']); policy['projects']['MAIN']['policy_activation_allowed'] = False
        data['store']._save_payload_to_state_table('effective_architecture_policy', policy)
    elif case == 'generation':
        with data['store'].db_manager.transaction() as conn:
            conn.execute('DELETE FROM artifact_facts WHERE artifact_name=?;', ('audit_findings_generation',))
    else:
        config = deepcopy(pipeline_policy.DYNAMIC_CONFIG)
        config.setdefault('architecture', {})['module_container'] = 'changed-runtime-config'
        monkeypatch.setattr(pipeline_policy, 'DYNAMIC_CONFIG', config)
    rows, total, ok = server._audit_violation_work_items_from_sqlite(data['raw'], page=1, page_size=20, project='MAIN')
    assert ok and total == 2 and len(rows) == 2
    assert all(x['finding_evidence']['status'] == 'unavailable' for x in rows)


@pytest.mark.parametrize('configured,fallback,expected,owner', [
    (['public.ts'], ['fallback.ts'], ['public.ts'], 'modular_doctrine'),
    ([], ['fallback.ts'], ['fallback.ts'], 'audit_policy'),
    (None, ['fallback.ts'], ['fallback.ts'], 'audit_policy'),
    ([None, '', 0], ['fallback.ts'], [], 'modular_doctrine')])
def test_module_v3_api_owner_precedence_preserved(configured, fallback, expected, owner):
    from tools.core import pipeline_policy
    with patch('tools.core.config.DOCTRINE', {'architectural_integrity_rules': {'api_entry_filenames': configured}}), \
         patch.object(pipeline_policy, 'get_audit_policy', return_value={'api_entry_filenames': fallback}):
        result = pipeline_policy.capture_api_entry_policy()
        assert pipeline_policy.get_api_entry_filenames() == result['names'] == expected
    assert result['owner'] == owner
    assert result['identity'] == evidence._import_evidence_digest({k: v for k, v in result.items() if k != 'identity'})


def test_module_v3_request_local_read_sharing(module_fixture):
    from tools.core import source_snapshot_reader, pipeline_policy
    data = module_fixture
    cache = {}
    with patch.object(source_snapshot_reader, 'read_source_snapshot_binding', wraps=read_source_snapshot_binding) as reader, \
         patch.object(pipeline_policy, 'capture_api_entry_policy', wraps=pipeline_policy.capture_api_entry_policy) as entries:
        for row in data['module_rows']:
            assert evidence.project_audit_import_subject(row, raw_dir=data['raw'], project='MAIN', rel_path=data['rel_path'], cache=cache)['status'] == 'bound_record_set'
        assert reader.call_count == entries.call_count == 1
        for row in data['module_rows']:
            assert evidence.project_audit_import_subject(row, raw_dir=data['raw'], project='MAIN', rel_path=data['rel_path'])['status'] == 'bound_record_set'
        assert reader.call_count == 3 and entries.call_count == 2


@pytest.mark.parametrize('case', ['type', 'dynamic', 'api_row_shape', 'wrong_language'])
def test_module_v3_builder_refuses_unsupported_context(module_fixture, case):
    data = module_fixture
    row = next(r for r in data['module_rows'] if r['rule'] == 'own_api_imports')
    e = deepcopy(row['finding_evidence']); record = deepcopy(e['subjects'][0]['record'])
    meta = deepcopy(data['meta'])
    if case in ['type', 'dynamic']: record['kind'] = case
    elif case == 'api_row_shape': e['module_trace']['api_entry_policy']['configured_entries'] = 'index.ts'
    else: meta['language'] = 'javascript' if meta['language'] == 'typescript' else 'typescript'
    result = evidence.build_audit_import_subject(project='MAIN', rel_path=data['rel_path'], file_data=meta,
        snapshot_id=data['commit']['snapshot_id'], source_binding=read_source_snapshot_binding(data['raw'], 'MAIN', data['rel_path']),
        policy=e['policy'], import_record=record, module_trace=e['module_trace'], rule_key=row['rule'])
    assert result['version'] == 'v3' and result['status'] == 'unavailable'


def test_module_v3_aggregation_preserves_version_budget_and_conflict(module_fixture):
    # Synthetic member IDs test aggregation only, not admitted source facts.
    base = module_fixture['module_rows'][0]['finding_evidence']
    combined = deepcopy(base)
    for index in range(evidence.audit_import_subject_contract()['max_subjects'] + 2):
        other = deepcopy(base); other['subjects'][0]['id'] = hashlib.sha256(str(index).encode()).hexdigest()
        combined = evidence.merge_audit_import_subjects(combined, other, apply_budget=False)
    combined = evidence.merge_audit_import_subjects(combined, combined)
    assert combined['version'] == 'v3' and combined['status'] == 'partial' and combined['omitted_subject_count'] > 0
    other = deepcopy(base); other['module_trace']['module_container'] = 'other'
    conflict = evidence.merge_audit_import_subjects(base, other)
    assert conflict['version'] == 'v3' and conflict['status'] == 'unavailable'


def test_module_v3_producer_does_not_activate_unapproved_policy(module_fixture, monkeypatch):
    data = module_fixture
    policy = deepcopy(data['policy']); policy['projects']['MAIN'].update(
        status='ADVISORY_AWAITING_HITL', policy_activation_allowed=False)
    output = []
    monkeypatch.setattr(audit, 'load_effective_architecture_policy_context', lambda *_: (policy, data['commit']['snapshot_id']))
    monkeypatch.setattr(audit, 'save_json_atomic', lambda _path, payload: output.append(deepcopy(payload)))
    with patch.object(audit, 'read_source_snapshot_binding', wraps=read_source_snapshot_binding) as reader:
        assert audit.analyze_project(changed_files=[f"MAIN::{data['rel_path']}"], atlas=data['atlas'])
        assert reader.call_count == 0
    assert not any(r['rule'] in ['deep_imports', 'own_api_imports'] for r in output[-1]['violations'])


@pytest.mark.parametrize('case', ['missing_trace', 'wrong_branch', 'numeric_bool', 'owner_pointer',
    'missing_version', 'unknown_version', 'extra_endpoint', 'partial_zero'])
def test_module_v3_production_schema_negatives(module_fixture, case):
    from tools.core.artifact_validator import _validate
    import jsonschema
    schema = json.loads((ROOT / 'config/schemas/audit_report.schema.json').read_text())
    e = deepcopy(next(r for r in module_fixture['module_rows'] if r['rule'] == 'own_api_imports')['finding_evidence'])
    if case == 'missing_trace': e.pop('module_trace')
    elif case == 'wrong_branch': e['module_trace']['decision_branch'] = 'cross_module_literal_api_path'
    elif case == 'numeric_bool': e['module_trace']['source_api_entry_exemption'] = 0
    elif case == 'owner_pointer': e['module_trace']['api_entry_policy'].pop('pointer')
    elif case == 'missing_version': e.pop('version')
    elif case == 'unknown_version': e['version'] = 'v99'
    elif case == 'extra_endpoint': e['target_layer'] = {'layer': 'ui'}
    else: e.update(status='partial', omitted_subject_count=0)
    slot = schema['properties']['violations']['items']['properties']['finding_evidence']
    errors = []; _validate(e, slot, schema, ['module'], errors)
    assert errors
    assert list(jsonschema.Draft7Validator({**schema, '$ref': '#/properties/violations/items/properties/finding_evidence'}).iter_errors(e))


@pytest.mark.parametrize('rel,raw,container,expected', [
    ('modules/a/x.ts', '@/modules/b/private', 'modules', True),
    ('modules/a/x.ts', '@/modules/b/api/', 'modules', False),
    ('modules/a/x.ts', '@/modules/b/api/index.ts', 'modules', True),
    ('modules/a/api/index.ts', '@/modules/a/api', 'modules', False),
    ('modules/a/x.ts', '@/modules/a/apiary', 'modules', True),
    ('modules/a/x.ts', 'pkg/@/modules/b/private', 'modules', True),
    ('src/modules/a/x.ts', '@/src/modules/b/private', 'src/modules', True),
    ('modules/a/x.ts', '../b/private', 'modules', False)])
def test_module_v3_shared_literal_decisions_preserved(rel, raw, container, expected):
    from tools.core.import_classifier import module_source_context, module_api_import_evidence
    from tools.core.pipeline_policy import capture_api_entry_policy
    decision = module_api_import_evidence(module_source_context(rel, container), raw,
        module_container=container, primary_alias='@/', api_entry_policy=capture_api_entry_policy())
    assert decision['violated'] == expected


def test_module_v3_nonmodule_guard_does_not_touch_unsupported_raw_value():
    from tools.core.import_classifier import module_source_context, module_api_import_evidence
    decision = module_api_import_evidence(module_source_context('outside/file.ts', 'modules'), 7,
        module_container='modules', primary_alias='@/')
    assert decision['violated'] is False
    assert decision['trace']['raw_api_terminal'] is None


@pytest.mark.parametrize('rel,container,index,module', [
    ('src/features/a/file.ts', 'src/features', 0, 'a'),
    ('packages/app/src/features/a/file.ts', 'src/features', 2, 'a'),
    ('src/features/a/src/features/b/file.ts', 'src/features', 0, 'a'),
    ('src/src/features/a/file.ts', 'src/features', 1, 'a'),
    ('one/two/three/a/file.js', 'one/two/three', 0, 'a'),
    ('src/not-features/a/file.ts', 'src/features', None, None),
    ('other-src/features/a/file.ts', 'src/features', None, None),
    ('src/nested/features/a/file.ts', 'src/features', None, None),
    ('src/features-old/a/file.ts', 'src/features', None, None),
    ('src/features', 'src/features', 0, None),
    ('src', 'src/features', None, None),
    ('Src/features/a/file.ts', 'src/features', None, None),
    ('src/features/a/file.ts', 'Src/features', None, None),
    ('src\\features\\a\\file.ts', 'src/features', None, None),
    ('src/features/a/file.ts', 'src\\features', None, None),
    ('modules/a/modules/b/file.ts', 'modules', 0, 'a'),
    ('modules', 'modules', 0, None),
    ('a/file.ts', '.', None, None),
    ('./a/file.ts', '.', 0, 'a'),
    ('/a/file.ts', '', 0, 'a'),
])
def test_module_container_exact_component_sequence(rel, container, index, module):
    from tools.core.import_classifier import module_source_context
    assert module_source_context(rel, container) == {
        'source_container_component_index': index, 'source_module': module, 'rel_path': rel}


@pytest.mark.parametrize('ext', ['ts', 'js'])
@pytest.mark.parametrize('case', ['matching', 'nested', 'near_name', 'noncontiguous', 'api_entry'])
def test_multi_component_container_scoped_pipeline(tmp_path, native_imports, monkeypatch, ext, case):
    from tools.core import fractal_policy, pipeline_policy
    # Exercise the declared runtime owner, not a second fixture resolver. Its
    # canonical slash value then feeds the actual Audit and carrier replay.
    configuration = deepcopy(fractal_policy.DYNAMIC_CONFIG)
    configuration.setdefault('architecture', {})['module_container'] = 'features\\catalog'
    monkeypatch.setattr(fractal_policy, 'DYNAMIC_CONFIG', configuration)
    monkeypatch.setattr(pipeline_policy, 'DYNAMIC_CONFIG', configuration)
    fractal_policy.get_module_container.cache_clear()
    monkeypatch.setattr(audit, 'get_module_root_name', pipeline_policy.get_module_root_name)
    rel = {'matching': f'features/catalog/a/MultiModule.{ext}',
        'nested': f'packages/app/features/catalog/a/MultiModule.{ext}',
        'near_name': f'features/catalog-old/a/MultiModule.{ext}',
        'noncontiguous': f'features/nested/catalog/a/MultiModule.{ext}',
        'api_entry': f'features/catalog/a/api/index.{ext}'}[case]
    try:
        data = _fixture(tmp_path, native_imports[f'MultiModule.{ext}'], monkeypatch,
            rel_path=rel, content=MULTI_MODULE_CONTENT, profile='FSD_STANDARD')
        assert fractal_policy.get_module_container() == 'features/catalog'
    finally:
        fractal_policy.get_module_container.cache_clear()
    rows = [r for r in data['report']['violations'] if r['rule'] in ['deep_imports', 'own_api_imports']]
    # The current doctrine declares index.ts, not index.js. Preserve its exact
    # exemption rather than inventing an extension-equivalent policy here.
    exempt_entry = case == 'api_entry' and ext == 'ts'
    expected = 0 if case in ['near_name', 'noncontiguous'] else 1 if exempt_entry else 2
    assert len(rows) == expected
    if not expected:
        return
    assert {r['rule'] for r in rows} == ({'deep_imports'} if exempt_entry else {'deep_imports', 'own_api_imports'})
    items, total, ok = server._audit_violation_work_items_from_sqlite(data['raw'], page=1, page_size=20, project='MAIN')
    integrity, itotal, iok = server._module_integrity_items_from_sqlite(data['raw'], f'MAIN::{rel}',
        max_items=20, target_root=str(data['root']))
    directives = contextos_mcp.build_agent_action_directives(audit_report={'violations': rows},
        raw_dir=data['raw'], _atlas_read=load_atlas_data_with_identity(data['raw']), max_items=20)
    assert ok and iok and total == itotal == expected
    for row in rows:
        payload = row['finding_evidence']
        assert payload['version'] == 'v3' and payload['status'] == 'bound_record_set'
        assert payload['module_trace']['source_module'] == 'a'
        assert payload['module_trace']['source_container_component_index'] == (2 if case == 'nested' else 0)
        for consumer in [items, integrity, directives]:
            assert next(r for r in consumer if r['rule'] == row['rule'])['finding_evidence'] == payload
        # Even a correctly re-signed caller cannot redefine the consumed path.
        forged = deepcopy(row)
        forged['finding_evidence']['module_trace']['source_container_component_index'] = 99
        resign(forged)
        assert evidence.project_audit_import_subject(forged, raw_dir=data['raw'], project='MAIN', rel_path=rel)['status'] == 'unavailable'
    # Removing physical snapshot proof cannot be rescued from the live file.
    with data['store'].db_manager.transaction() as conn:
        conn.execute('DELETE FROM source_snapshots;')
    items, total, ok = server._audit_violation_work_items_from_sqlite(data['raw'], page=1, page_size=20, project='MAIN')
    assert ok and total == expected and data['source'].exists()
    assert all(r['finding_evidence']['status'] == 'unavailable' for r in items)


def test_actual_audit_sqlite_queue_and_directive_transport(fixture):
    rows = fixture['rows']
    view = next(r for r in rows if 'widgets/view' in r['detail'])
    assert {s['record']['name'] for s in view['finding_evidence']['subjects']} == {'View', 'Style'}
    assert view['finding_evidence']['status'] == 'bound_record_set'
    assert view['finding_evidence']['mapping']['pointer'].startswith('/architectural_integrity_rules/')
    queue, total, ok = server._audit_violation_work_items_from_sqlite(fixture['raw'], page=1, page_size=20, project='MAIN', rule=RULE)
    assert ok and total == len(rows) == len(queue)
    assert all(r['finding_evidence']['status'] == 'bound_record_set' for r in queue)
    integrity, count, ok = server._module_integrity_items_from_sqlite(fixture['raw'], 'MAIN::domain/Panel.ts',
        max_items=20, target_root=str(fixture['root']))
    assert ok and count == len(fixture['report']['violations'])
    assert all(r['finding_evidence']['status'] == 'bound_record_set' for r in integrity if r['rule'] == RULE)
    directives = contextos_mcp.build_agent_action_directives(audit_report={'violations': rows},
        raw_dir=fixture['raw'], _atlas_read=load_atlas_data_with_identity(fixture['raw']), max_items=20)
    assert len(directives) == len(rows)
    assert all(r['finding_evidence']['status'] == 'bound_record_set' for r in directives)
    for item in queue + directives:
        assert item['finding_evidence']['causal_relation'] == 'not_evaluated'
        assert item['finding_evidence']['runtime_proof'] == 'not_performed'
        assert item['mutation_authority'] == 'separate_actor_policy_and_approval'
        assert all(s['source_range'] is None for s in item['finding_evidence']['subjects'])
        assert 'effective_target_policy' not in item['finding_evidence']['policy']['runtime_context']
    assert all(r['atlas_rel_path'] == 'domain/Panel.ts' and r['repo_relative_path'] == 'src/domain/Panel.ts' for r in rows)


@pytest.mark.parametrize('case', ['snapshot', 'project', 'path', 'source_hash', 'unknown_field', 'runtime', 'span', 'rule_mode', 'approval', 'policy_pointer'])
def test_resealed_caller_claim_does_not_admit_new_authority(fixture, case):
    row = deepcopy(fixture['rows'][0]); carrier = row['finding_evidence']
    if case == 'snapshot': carrier['atlas_snapshot_id'] = 'other'
    elif case == 'project': carrier['project_key'] = 'OTHER'
    elif case == 'path': carrier['atlas_rel_path'] = 'widgets/view.ts'
    elif case == 'source_hash': carrier['source_hash'] = '0' * 64
    elif case == 'unknown_field': carrier['safe_to_apply'] = True
    elif case == 'runtime': carrier['runtime_proof'] = 'confirmed'
    elif case == 'span': carrier['subjects'][0]['source_range'] = {'start_line': 1}
    elif case == 'rule_mode': carrier['policy']['effective_rule']['mode'] = 'critical'
    elif case == 'approval': carrier['policy']['source_project']['approval_entry_id'] = 'fake'
    elif case == 'policy_pointer': carrier['mapping']['pointer'] = '/architectural_integrity_rules/global_rules/0'
    policy = carrier['policy']; policy['identity'] = evidence._import_evidence_digest({k: v for k, v in policy.items() if k != 'identity'})
    resign(row)
    assert project(fixture, row)['status'] == 'unavailable'
    assert len(fixture['report']['violations']) >= len(fixture['rows'])


@pytest.mark.parametrize('case', ['missing', 'content', 'atlas_hash', 'oversized'])
def test_physical_snapshot_failure_has_no_live_or_shadow_rescue(fixture, case):
    store = fixture['store']
    with store.db_manager.transaction() as conn:
        if case == 'missing': conn.execute('DELETE FROM source_snapshots;')
        elif case == 'content': conn.execute("UPDATE source_snapshots SET content = 'changed';")
        elif case == 'atlas_hash': conn.execute("UPDATE files SET hash = ?;", ('0'*64,))
        else: conn.execute("UPDATE source_snapshots SET status = 'oversized';")
    assert fixture['source'].exists()
    assert project(fixture)['status'] == 'unavailable'


@pytest.mark.parametrize('case', ['unsealed', 'stale', 'profile', 'generation'])
def test_current_policy_and_generation_refusals_preserve_findings(fixture, case):
    policy = deepcopy(fixture['policy'])
    if case == 'unsealed': policy['projects']['MAIN']['policy_activation_allowed'] = False
    elif case == 'stale': policy['invalidation']['atlas_snapshot_id'] = 'stale'
    elif case == 'profile': policy['projects']['MAIN']['recommended_profile'] = 'MODULAR_FLAT'
    else:
        with fixture['store'].db_manager.transaction() as conn:
            conn.execute('DELETE FROM artifact_facts WHERE artifact_name=?;', ('audit_findings_generation',))
    fixture['store']._save_payload_to_state_table('effective_architecture_policy', policy)
    assert project(fixture)['status'] == 'unavailable'
    queue, count, ok = server._audit_violation_work_items_from_sqlite(fixture['raw'], page=1, page_size=20, rule=RULE)
    assert ok and count == len(fixture['rows']) and queue
    assert all(r['finding_evidence']['status'] == 'unavailable' for r in queue)


def test_legacy_queue_and_independent_findings_not_removed(fixture):
    row = deepcopy(fixture['rows'][0]); row.pop('finding_evidence')
    assert project(fixture, row)['reason'] == 'legacy_evidence_missing'
    existing = len(fixture['report']['violations'])
    assert len(audit._dedupe_violations({RULE: [row, deepcopy(row)]})[RULE]) == 1
    assert existing == len(fixture['report']['violations'])


def test_request_scoped_source_read_once_no_cross_request_cache(fixture):
    from tools.core import source_snapshot_reader
    cache = {}
    with patch.object(source_snapshot_reader, 'read_source_snapshot_binding', wraps=read_source_snapshot_binding) as reader:
        for row in fixture['rows']: assert project(fixture, row, cache=cache)['status'] == 'bound_record_set'
        assert reader.call_count == 1
        assert project(fixture)['status'] == 'bound_record_set'
        assert reader.call_count == 2
    assert all('content' not in value[1] for key, value in cache.items() if len(key) == 3)


def test_actual_producer_preserves_existing_unsealed_policy_gate(fixture, monkeypatch):
    policy = deepcopy(fixture['policy']); policy['projects']['MAIN'].update(
        status='ADVISORY_AWAITING_HITL', policy_activation_allowed=False)
    output = []
    monkeypatch.setattr(audit, 'load_effective_architecture_policy_context', lambda *_: (policy, fixture['commit']['snapshot_id']))
    monkeypatch.setattr(audit, 'save_json_atomic', lambda _path, payload: output.append(deepcopy(payload)))
    with patch.object(audit, 'read_source_snapshot_binding', wraps=read_source_snapshot_binding) as reader:
        assert audit.analyze_project(changed_files=['MAIN::domain/Panel.ts'], atlas=fixture['atlas'])
        assert reader.call_count == 0
    assert not any(r['rule'] == RULE for r in output[-1]['violations'])
    assert output[-1]['architecture_policy_application']['architecture_incomplete_projects'] == ['MAIN']


def test_typed_schema_and_record_budget(fixture):
    import jsonschema
    schema = json.loads((ROOT / 'config/schemas/audit_report.schema.json').read_text(encoding='utf-8'))
    jsonschema.validate(fixture['report'], schema)
    from tools.core.artifact_validator import validate_payload
    assert not validate_payload('audit_report', fixture['report'])
    base = fixture['rows'][0]['finding_evidence']; merged = deepcopy(base)
    for index in range(evidence.audit_import_subject_contract()['max_subjects'] + 2):
        other = deepcopy(base); other['subjects'][0]['id'] = hashlib.sha256(str(index).encode()).hexdigest()
        merged = evidence.merge_audit_import_subjects(merged, other, apply_budget=False)
    merged = evidence.merge_audit_import_subjects(merged, merged)
    assert merged['status'] == 'partial' and merged['omitted_subject_count'] > 0
    assert len(merged['subjects']) == evidence.audit_import_subject_contract()['max_subjects']


@pytest.mark.parametrize('case', ['missing_bound_fields', 'empty_bound_set', 'missing_unavailable_reason', 'unavailable_with_subjects', 'runtime_promotion'])
def test_production_schema_enforces_ref_and_conditional_guards(fixture, case):
    from tools.core.artifact_validator import validate_payload
    report = deepcopy(fixture['report']); row = next(r for r in report['violations'] if r['rule'] == RULE)
    carrier = row['finding_evidence']
    if case == 'missing_bound_fields': carrier.pop('policy')
    elif case == 'empty_bound_set': carrier['subjects'] = []
    elif case == 'missing_unavailable_reason':
        row['finding_evidence'] = evidence.unavailable_audit_import_subject('fixture'); row['finding_evidence'].pop('reason')
    elif case == 'unavailable_with_subjects': carrier.update(status='unavailable', reason='fixture')
    else: carrier['runtime_proof'] = 'confirmed'
    assert validate_payload('audit_report', report)


def test_duplicate_json_cannot_become_bound_subject_or_generation(fixture):
    with fixture['store'].db_manager.transaction() as conn:
        conn.execute('UPDATE findings SET message=? WHERE code=?;',
            ('{"finding_evidence":null,' + json.dumps(fixture['rows'][0], ensure_ascii=False)[1:], RULE))
    queue, count, ok = server._audit_violation_work_items_from_sqlite(fixture['raw'], page=1, page_size=20, rule=RULE)
    assert ok and count == len(fixture['rows'])
    assert all(r['finding_evidence']['status'] == 'unavailable' for r in queue)
    with fixture['store'].db_manager.transaction() as conn:
        current = conn.execute('SELECT fact_value FROM artifact_facts WHERE artifact_name=?;', ('audit_findings_generation',)).fetchone()[0]
        conn.execute('UPDATE artifact_facts SET fact_value=? WHERE artifact_name=?;',
            ('{"snapshot_id":"wrong",' + current[1:], 'audit_findings_generation'))
    assert evidence.read_audit_finding_manifest(fixture['raw']) == {}


def test_dedupe_caps_once_after_unique_member_union(fixture):
    # Synthetic aggregation only; these names are not admitted source facts.
    base = fixture['rows'][0]; cap = evidence.audit_import_subject_contract()['max_subjects']
    rows = []
    for index in range(cap + 3):
        row = deepcopy(base)
        row['finding_evidence']['subjects'] = [row['finding_evidence']['subjects'][0]]
        row['finding_evidence']['subjects'][0]['id'] = hashlib.sha256(str(index).encode()).hexdigest()
        rows.append(row)
    duplicated = [*rows, *deepcopy(rows)]
    first = audit._dedupe_violations({RULE: deepcopy(duplicated)})[RULE][0]['finding_evidence']
    second = audit._dedupe_violations({RULE: list(reversed(deepcopy(duplicated)))})[RULE][0]['finding_evidence']
    assert first == second and first['omitted_subject_count'] == 3
    assert len(first['subjects']) == cap and first['status'] == 'partial'


@pytest.mark.parametrize('parser', [None, 'observed', {'status':'degraded'}])
def test_bad_parser_metadata_does_not_crash_additive_producer(fixture, parser):
    old = fixture['rows'][0]['finding_evidence']; meta = deepcopy(fixture['meta']); meta['parser_evidence'] = parser
    result = evidence.build_audit_import_subject(project='MAIN', rel_path='domain/Panel.ts', file_data=meta,
        snapshot_id=fixture['commit']['snapshot_id'], source_binding=read_source_snapshot_binding(fixture['raw'], 'MAIN', 'domain/Panel.ts'),
        policy=old['policy'], import_record=old['subjects'][0]['record'], source_layer=old['source_layer'],
        target_layer=old['target_layer'], mapping=old['mapping'])
    assert result['status'] == 'unavailable'


@pytest.mark.parametrize('kind,scope', [('type','top_level'), ('dynamic','local'), ('require','top_level'), ('reexport','top_level')])
def test_unsupported_records_are_not_suppression(fixture, kind, scope):
    old = fixture['rows'][0]['finding_evidence']; record = deepcopy(old['subjects'][0]['record'])
    record.update(kind=kind, scope=scope)
    candidate = evidence.build_audit_import_subject(project='MAIN', rel_path='domain/Panel.ts', file_data=fixture['meta'],
        snapshot_id=fixture['commit']['snapshot_id'], source_binding=read_source_snapshot_binding(fixture['raw'], 'MAIN', 'domain/Panel.ts'),
        policy=old['policy'], import_record=record, source_layer=old['source_layer'], target_layer=old['target_layer'], mapping=old['mapping'])
    assert candidate['status'] == 'unavailable'
    assert evidence.merge_audit_import_subjects(old, candidate)['status'] == 'unavailable'


def test_source_reader_is_exact_selected_db_and_accepts_empty_text(fixture, tmp_path):
    assert read_source_snapshot_binding(tmp_path / 'absent', 'MAIN', 'domain/Panel.ts')['status'] == 'missing'
    assert read_source_snapshot_binding(fixture['raw'], 'OTHER', 'domain/Panel.ts')['status'] == 'missing'
    assert read_source_snapshot_binding(fixture['raw'], 'MAIN', 'src/domain/Panel.ts')['status'] == 'missing'
    assert read_source_snapshot_binding(fixture['raw'], 'MAIN', 'domain/Panel.ts', expected_hash='err')['status'] == 'atlas_mismatch'
    digest = hashlib.sha256(b'').hexdigest()
    with fixture['store'].db_manager.transaction() as conn:
        conn.execute('UPDATE source_snapshots SET content=?,content_hash=?;', ('', digest))
        conn.execute('UPDATE files SET hash=?;', (digest,))
    result = read_source_snapshot_binding(fixture['raw'], 'MAIN', 'domain/Panel.ts')
    assert result == {'status': 'ok', 'content': '', 'source_hash': digest}


def test_js_parser_shape_is_available_without_target_execution(native_imports):
    assert native_imports['.js']['status'] == 'observed'
    assert native_imports['.js']['records'] == native_imports['.ts']['records']


def test_layer_trace_retains_current_path_and_mapping_precedence(monkeypatch):
    doctrine = {'layer_rules': [{'layer':'domain','conditions':{'path_fragments':['domain']}}],
        'architectural_integrity_rules': {'global_rules':[{'source':'domain','targets':['infra'],'scope':['typescript']}],
            'language_specific_rules':{'javascript':[{'source':'domain','targets':['infra']}]},
            'forbidden_mappings':[{'source':'domain','targets':['infra']}],
            'layer_identification_hints':[{'fragment':'infra','layer':'infra'}]}}
    monkeypatch.setattr(layer_resolver, 'DOCTRINE', doctrine)
    assert layer_resolver.resolve_layer('domain/a.ts') == 'domain'
    assert layer_resolver.resolve_layer('app/[domain]/page.tsx') == 'unknown'
    for language, suffix in [('typescript','global_rules/0'), ('javascript','language_specific_rules/javascript/0'), ('go','forbidden_mappings/0')]:
        trace = layer_resolver.layer_violation_evidence('domain', 'infra', language)
        assert trace['pointer'].endswith(suffix) and layer_resolver.is_violation('domain','infra',language)
    assert not layer_resolver.is_violation('infra','domain')


def _restriction_row(data):
    return (data.get('restriction_rows') or [r for r in data['report']['violations'] if r['rule'] == 'domain_ui_leaks'])[0]


def _project_restriction(data, row=None, **kwargs):
    return evidence.project_audit_import_subject(row or _restriction_row(data), raw_dir=data['raw'],
        project='MAIN', rel_path=data['rel_path'], **kwargs)


def test_restriction_actual_audit_sqlite_and_three_projections(restriction_fixture):
    import jsonschema
    from tools.core.artifact_validator import validate_payload
    data = restriction_fixture; rows = data['restriction_rows']; rule = data['restriction_rule']
    assert not validate_payload('audit_report', data['report'])
    jsonschema.validate(data['report'], json.loads((ROOT / 'config/schemas/audit_report.schema.json').read_text(encoding='utf-8')))
    assert all(_project_restriction(data, row)['status'] == 'bound_record_set' for row in rows)
    kinds = {s['record']['kind'] for row in rows for s in row['finding_evidence']['subjects']}
    assert kinds == {'named', 'default', 'namespace', 'side_effect'}
    queue, count, ok = server._audit_violation_work_items_from_sqlite(data['raw'], page=1, page_size=20, project='MAIN', rule=rule)
    assert ok and count == len(rows) == len(queue)
    integrity, total, ok = server._module_integrity_items_from_sqlite(data['raw'], f'MAIN::{data["rel_path"]}',
        max_items=20, target_root=str(data['root']))
    assert ok and total == len(data['report']['violations'])
    directives = contextos_mcp.build_agent_action_directives(audit_report={'violations': rows},
        raw_dir=data['raw'], _atlas_read=load_atlas_data_with_identity(data['raw']), max_items=20)
    assert len(directives) == len(rows)
    for item in queue + directives + [r for r in integrity if r['rule'] == rule]:
        carrier = item['finding_evidence']
        assert carrier['version'] == 'v2' and carrier['status'] == 'bound_record_set'
        assert carrier['rule'] == rule and 'target_layer' not in carrier
        assert carrier['restriction_trace']['matched_source_glob'] is None
        assert carrier['restriction_trace']['candidate_field'] == 'source'
        assert carrier['causal_relation'] == 'not_evaluated' and carrier['runtime_proof'] == 'not_performed'
    for item in queue + directives:
        assert item['mutation_authority'] == 'separate_actor_policy_and_approval'
    for row in data['report']['violations']:
        if row['rule'] == RULE:
            assert row['finding_evidence']['version'] == 'v1' and _project_restriction(data, row)['status'] == 'bound_record_set'


@pytest.mark.parametrize('case', ['version', 'kind', 'rule', 'decision', 'snapshot', 'source_hash', 'pointer',
    'fragment', 'candidate', 'row', 'span', 'extra', 'runtime', 'policy', 'primary_alias', 'fragment_type', 'violated_type'])
def test_restriction_resealed_context_must_match_producer_and_actual_decision(fixture, case):
    row = deepcopy(_restriction_row(fixture)); carrier = row['finding_evidence']
    trace = carrier['restriction_trace']
    if case == 'version': carrier['version'] = 'v1'
    elif case == 'kind': carrier['kind'] = 'other'
    elif case == 'rule': carrier['rule'] = 'impure_entities'
    elif case == 'decision': carrier['decision_kind'] = 'alias_boundary'
    elif case == 'snapshot': carrier['atlas_snapshot_id'] = 'different'
    elif case == 'source_hash': carrier['source_hash'] = '0'*64
    elif case == 'pointer': trace['pointer'] = '/architectural_integrity_rules/layer_import_restrictions/0'
    elif case == 'fragment': trace['fragment_index'] = 0; trace['fragment'] = 'infra'
    elif case == 'candidate': trace['candidate_field'] = 'raw_source'
    elif case == 'row': trace['row']['forbidden_substrings'] = ['wrong']
    elif case == 'span': carrier['subjects'][0]['source_range'] = {'start_line': 1}
    elif case == 'extra': carrier['safe_to_apply'] = True
    elif case == 'runtime': carrier['runtime_proof'] = 'confirmed'
    elif case == 'policy': carrier['policy']['effective_rule']['mode'] = 'disabled'
    elif case == 'fragment_type': trace['fragment_index'] = float(trace['fragment_index'])
    elif case == 'violated_type': trace['violated'] = 1
    else:
        trace['primary_alias'] = '~/'
        carrier['policy']['runtime_context']['primary_alias'] = '~/'
    policy = carrier['policy']; policy['identity'] = evidence._import_evidence_digest({k:v for k,v in policy.items() if k != 'identity'})
    resign(row)
    assert _project_restriction(fixture, row)['status'] == 'unavailable'
    if case != 'primary_alias':
        # Current primary presence is necessary but insufficient: adversarial
        # stored fields still fail current source/policy/decision reconstruction.
        with fixture['store'].db_manager.transaction() as conn:
            conn.execute('UPDATE findings SET message=? WHERE code=? AND message=?;',
                (evidence.canonical_audit_finding_message(row), 'domain_ui_leaks',
                 evidence.canonical_audit_finding_message(_restriction_row(fixture))))
        assert _project_restriction(fixture, row)['status'] == 'unavailable'
    queue, count, ok = server._audit_violation_work_items_from_sqlite(fixture['raw'], page=1, page_size=20, rule='domain_ui_leaks')
    assert ok and count == len([r for r in fixture['report']['violations'] if r['rule'] == 'domain_ui_leaks'])


@pytest.mark.parametrize('case', ['missing_snapshot', 'content', 'hash', 'oversized', 'policy', 'generation', 'finding'])
def test_restriction_primary_failures_preserve_ordinary_findings(fixture, case):
    with fixture['store'].db_manager.transaction() as conn:
        if case == 'missing_snapshot': conn.execute('DELETE FROM source_snapshots;')
        elif case == 'content': conn.execute("UPDATE source_snapshots SET content='changed';")
        elif case == 'hash': conn.execute('UPDATE files SET hash=?;', ('0'*64,))
        elif case == 'oversized': conn.execute("UPDATE source_snapshots SET status='oversized';")
        elif case == 'generation': conn.execute('DELETE FROM artifact_facts WHERE artifact_name=?;', ('audit_findings_generation',))
        elif case == 'finding': conn.execute('DELETE FROM findings WHERE code=?;', ('domain_ui_leaks',))
    if case == 'policy':
        policy = deepcopy(fixture['policy']); policy['projects']['MAIN']['policy_activation_allowed'] = False
        fixture['store']._save_payload_to_state_table('effective_architecture_policy', policy)
    assert _project_restriction(fixture)['status'] == 'unavailable'
    assert fixture['source'].exists()
    if case not in {'finding', 'generation'}:
        queue, total, ok = server._audit_violation_work_items_from_sqlite(fixture['raw'], page=1, page_size=20, rule='domain_ui_leaks')
        assert ok and total > 0 and all(r['finding_evidence']['status'] == 'unavailable' for r in queue)


def test_restriction_shared_request_read_and_legacy_status(fixture):
    rows = [r for r in fixture['report']['violations'] if r['rule'] in {RULE, 'domain_ui_leaks'}]
    cache = {}
    with patch('tools.core.source_snapshot_reader.read_source_snapshot_binding', wraps=read_source_snapshot_binding) as reader:
        for row in rows:
            assert _project_restriction(fixture, row, cache=cache)['status'] == 'bound_record_set'
        assert reader.call_count == 1
        assert _project_restriction(fixture)['status'] == 'bound_record_set'
        assert reader.call_count == 2
    row = deepcopy(_restriction_row(fixture)); row.pop('finding_evidence')
    result = _project_restriction(fixture, row)
    assert result['version'] == 'v2' and result['reason'] == 'legacy_evidence_missing'
    base = _restriction_row(fixture)['finding_evidence']; merged = deepcopy(base)
    for index in range(evidence.audit_import_subject_contract()['max_subjects'] + 2):
        other = deepcopy(base); other['subjects'][0]['id'] = hashlib.sha256(str(index).encode()).hexdigest()
        merged = evidence.merge_audit_import_subjects(merged, other, apply_budget=False)
    merged = evidence.merge_audit_import_subjects(merged, merged)
    assert merged['version'] == 'v2' and merged['status'] == 'partial' and merged['omitted_subject_count'] > 0
    other = deepcopy(base); other['restriction_trace']['pointer'] += '/different'
    refused = evidence.merge_audit_import_subjects(base, other)
    assert refused['version'] == 'v2' and refused['status'] == 'unavailable'


def test_restriction_js_entities_are_not_widened(tmp_path, native_imports, monkeypatch):
    data = _fixture(tmp_path, native_imports['Entity.js'], monkeypatch,
        rel_path='entities/Entity.js', content=ENTITY_CONTENT)
    assert not [r for r in data['report']['violations'] if r['rule'] == 'impure_entities']


@pytest.mark.parametrize('case', ['version', 'unknown_version', 'decision', 'rule', 'trace', 'policy', 'span',
    'runtime', 'empty_bound', 'partial_zero', 'bound_omitted', 'unavailable_records', 'unavailable_reason', 'violated_type'])
def test_restriction_production_schema_guards(fixture, case):
    import jsonschema
    from tools.core.artifact_validator import validate_payload
    report = deepcopy(fixture['report']); row = next(r for r in report['violations'] if r['rule'] == 'domain_ui_leaks')
    c = row['finding_evidence']
    if case == 'version': c.pop('version')
    elif case == 'unknown_version': c['version'] = 'v3'
    elif case == 'decision': c['decision_kind'] = 'other'
    elif case == 'rule': c.pop('rule')
    elif case == 'trace': c.pop('restriction_trace')
    elif case == 'policy': c.pop('policy')
    elif case == 'span': c['subjects'][0]['source_range'] = {'start_line': 1}
    elif case == 'runtime': c['runtime_proof'] = 'confirmed'
    elif case == 'empty_bound': c['subjects'] = []
    elif case == 'partial_zero': c.update(status='partial', omitted_subject_count=0)
    elif case == 'bound_omitted': c['omitted_subject_count'] = 1
    elif case == 'violated_type': c['restriction_trace']['violated'] = 1
    elif case == 'unavailable_records': c.update(status='unavailable', reason='fixture')
    else:
        row['finding_evidence'] = evidence.unavailable_audit_import_subject('fixture', rule_key='domain_ui_leaks')
        row['finding_evidence'].pop('reason')
    assert validate_payload('audit_report', report)
    schema = json.loads((ROOT / 'config/schemas/audit_report.schema.json').read_text(encoding='utf-8'))
    assert list(jsonschema.Draft7Validator(schema).iter_errors(report))


@pytest.mark.parametrize('source,raw,fragment,field', [
    ('react', 'react', 'react', 'source'), ('reactivity', 'reactivity', 'react', None),
    ('next/link', 'next/link', 'next/', 'source'), ('nextjs/link', 'nextjs/link', 'next/', None),
    ('domain/platformPorts.ts', '@/domain/platformPorts', 'platform', None),
    ('UI\\View.ts', '@/ui/view', 'ui', 'source'), ('domain/port', '@/widgets/view', 'widgets', 'raw_source'),
    ('ui/view', '@/widgets/view', 'widgets', 'raw_source'), ('@scope/pkg/view', '@scope/pkg/view', '@scope/pkg', 'source'),
    ('', './src/../ui/view', 'ui', 'raw_source'), ('src/superui/view', 'src/superui/view', 'ui', None),
    ('src/ui/view', '', '', None)])
def test_restriction_segment_trace_retains_existing_boolean(source, raw, fragment, field):
    assert layer_resolver._import_fragment_candidate(source, raw, fragment) == field
    assert audit._import_matches_forbidden_fragment(source, raw, fragment) == (field is not None)


def test_restriction_trace_precedence_language_exemption_and_all_rows():
    rows = evidence.audit_import_restriction_rows()
    domain = rows[1]
    kwargs = dict(rel_path='src/domain/leaf.ts', source_layer='domain/logic',
                  import_path='ui/view', raw_import_path='@/infra/client', language='typescript', primary_alias='@/')
    trace = layer_resolver.layer_import_restriction_evidence(domain, 1, **kwargs)
    assert trace['violated'] and trace['fragment'] == 'infra' and trace['candidate_field'] == 'raw_source'
    trace = layer_resolver.layer_import_restriction_evidence(domain, 1,
        **{**kwargs, 'rel_path': 'src/domain/schemas/platformPorts.ts'})
    assert not trace['violated'] and trace['matched_source_glob'] == domain['allowed_source_globs'][0]
    assert audit._restriction_allows_source(domain, 'src/domain/schemas/platformPorts.ts')
    assert layer_resolver.resolve_layer_evidence('app/[domain]/page.tsx')['layer'] != 'domain/logic'
    entity = layer_resolver.layer_import_restriction_evidence(rows[0], 0,
        **{**kwargs, 'source_layer': 'entities', 'language': 'javascript', 'import_path': 'react', 'raw_import_path': 'react'})
    assert not entity['violated']
    duplicate = {**domain, 'forbidden_substrings': ['ui']}
    # Every declaration can emit independently; dedupe refuses colliding trace
    # authority rather than changing the producer's existing finding counts.
    assert all(layer_resolver.layer_import_restriction_evidence(row, i, **kwargs)['violated']
               for i, row in enumerate([domain, duplicate]))


@pytest.mark.parametrize('case', ['ast', 'parser', 'language', 'type', 'dynamic', 'local', 'legacy'])
def test_restriction_builder_refuses_unsupported_evidence(fixture, case):
    carrier = _restriction_row(fixture)['finding_evidence']
    meta = deepcopy(fixture['meta']); record = deepcopy(carrier['subjects'][0]['record'])
    if case == 'ast': meta['ast_contract_version'] = 'old'
    elif case == 'parser': meta['parser_evidence'] = {'status': 'degraded'}
    elif case == 'language': meta['language'] = 'javascript'
    elif case == 'type': record['kind'] = 'type'
    elif case == 'dynamic': record['kind'] = 'dynamic'
    elif case == 'local': record['scope'] = 'local'
    else: record = {'source': record['source'], 'raw_source': record['raw_source'], 'name': '*', 'kind': 'module'}
    result = evidence.build_audit_import_subject(project='MAIN', rel_path=fixture['rel_path'], file_data=meta,
        snapshot_id=fixture['commit']['snapshot_id'], source_binding=read_source_snapshot_binding(fixture['raw'], 'MAIN', fixture['rel_path']),
        policy=carrier['policy'], import_record=record, source_layer=carrier['source_layer'],
        restriction_trace=carrier['restriction_trace'], rule_key='domain_ui_leaks')
    assert result['version'] == 'v2' and result['status'] == 'unavailable'

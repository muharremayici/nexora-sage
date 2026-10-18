"""Source-bound measurement compatibility, never native lint/runtime proof."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from tools.core.pipeline_policy import resolve_loc_finding_semantics
from tools.core.target_policy_profile import aggregate_effective_target_policy, inventory_project_target_policy
from tools.engines.generate_atlas import _normalize_polyglot_symbols
from tools.engines import mcp_governance_engine as mcp

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ('// 😀 exact source coordinates\r\nexport function plain() {\r\n'
    '    // counted comment\r\n\r\n    return 1;\r\n}\r\n'
    'export const direct = () => { return 2; };\r\n'
    'export const split =\r\n    () => { return 3; };\r\n'
    'export const wrapped = (() => 1)();\r\n')


def _policy(tmp_path, tool='eslint', *, flags=None, version='9.39.1'):
    package = {'devDependencies': {'eslint' if tool == 'eslint' else '@biomejs/biome': version}}
    (tmp_path / 'package.json').write_text(json.dumps(package), encoding='utf-8')
    options = {'max' if tool == 'eslint' else 'maxLines': 2, **(flags or {})}
    payload = ({'rules': {'max-lines-per-function': ['error', options]}} if tool == 'eslint' else
        {'$schema': 'https://biomejs.dev/schemas/2.2.7/schema.json', 'linter': {'rules': {
            'complexity': {'noExcessiveLinesPerFunction': {'level': 'error', 'options': options}}}}})
    (tmp_path / ('.eslintrc.json' if tool == 'eslint' else 'biome.json')).write_text(json.dumps(payload), encoding='utf-8')
    profile = inventory_project_target_policy(tmp_path, project='MAIN', workspace_root=tmp_path,
        package_json=package, package_path=tmp_path / 'package.json')
    return json.loads(json.dumps(aggregate_effective_target_policy({'MAIN': profile})))


@pytest.fixture(scope='module')
def sequenced(tmp_path_factory):
    path = tmp_path_factory.mktemp('locm') / 'probe.ts'
    path.write_bytes(SOURCE.encode('utf-8'))
    result = subprocess.run(['node', str(ROOT/'tools/engines/ast_sequencer.cjs'), str(path)],
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert result.returncode == 0, result.stderr
    raw = json.loads(result.stdout)
    symbols = _normalize_polyglot_symbols(raw, SOURCE, 'typescript')
    return raw, {row['name']: row for row in symbols}, hashlib.sha256(SOURCE.encode('utf-8')).hexdigest()


def test_missing_measurement_cannot_turn_a_declared_number_into_a_symbol_threshold(tmp_path):
    finding = resolve_loc_finding_semantics('function', 6, project='MAIN', file_path='probe.ts',
        effective_target_policy=_policy(tmp_path))
    assert finding['limit'] == 150 and finding['limit_authority'] == 'sage_default'
    assert finding['target_policy_resolution']['matched_rules'][0]['limit'] == 2
    assert finding['target_policy_resolution']['unresolved_reasons']


def test_biome_count_is_not_a_declaration_span_metric(tmp_path):
    finding = resolve_loc_finding_semantics('function', 6, project='MAIN', file_path='probe.ts',
        effective_target_policy=_policy(tmp_path, 'biome', version='2.2.7'))
    assert finding['limit'] == 150
    assert finding['target_policy_resolution']['matched_rules'][0]['limit'] == 2


def test_parser_emits_source_bound_measurement_not_a_normalized_kind_guess(sequenced):
    _, symbols, identity = sequenced
    fact = symbols['plain'].get('loc_measurement_evidence')
    assert isinstance(fact, dict) and fact['status'] == 'observed'
    assert fact['source_hash'] == identity
    assert fact['offset_unit'] == 'utf8_bytes'
    assert fact['spans']['declaration']['line'] == 2
    assert symbols['split']['loc_measurement_evidence']['spans']['callable']['line'] == 9
    assert 'loc_measurement_evidence' not in symbols['wrapped']


def _finding(policy, symbol, *, identity=None, observed=None):
    from tools.engines.generate_atlas import _atlas_text_hash
    return resolve_loc_finding_semantics(symbol['canonical_symbol_type'],
        symbol['end_line'] - symbol['line'] + 1 if observed is None else observed,
        symbol_name=symbol['name'], start_line=symbol['line'], end_line=symbol['end_line'],
        project='MAIN', file_path='probe.ts', effective_target_policy=policy,
        measurement_context={'symbol': symbol, 'source_hash': identity or _atlas_text_hash(SOURCE)})


@pytest.mark.parametrize('name,expected', [('plain', 2), ('direct', 2), ('split', 150)])
def test_actual_source_span_controls_threshold_without_changing_observed_loc(tmp_path, sequenced, name, expected):
    symbol = sequenced[1][name]
    finding = _finding(_policy(tmp_path), symbol)
    assert finding['limit'] == expected
    assert finding['observed_loc'] == symbol['end_line'] - symbol['line'] + 1
    resolution = finding['target_policy_resolution']
    assert resolution['native_execution'] == 'not_run'
    assert resolution['measurement_compatibility'][0]['status'] == ('bounded_static_compatible' if expected == 2 else 'unresolved')


@pytest.mark.parametrize('flags', [{'skipBlankLines': True}, {'skipComments': True}])
def test_counting_flags_do_not_silently_reinterpret_declaration_lines(tmp_path, sequenced, flags):
    finding = _finding(_policy(tmp_path, flags=flags), sequenced[1]['plain'])
    assert finding['limit'] == 150 and finding['observed_loc'] == 5
    assert finding['target_policy_resolution']['unresolved_reasons'] == ['counting_options_not_declaration_lines']


@pytest.mark.parametrize('version', ['9.39.0', '8.57.0', '^9.39.1'])
def test_unreviewed_declared_semantics_are_not_inferred_from_a_major(tmp_path, sequenced, version):
    finding = _finding(_policy(tmp_path, version=version), sequenced[1]['plain'])
    assert finding['limit'] == 150 and finding['target_policy_resolution']['unresolved_reasons']


@pytest.mark.parametrize('mutation', ['source', 'version', 'unit', 'kind', 'extra', 'line', 'observed', 'missing'])
def test_missing_stale_foreign_or_malformed_normalized_facts_fall_back(tmp_path, sequenced, mutation):
    symbol = deepcopy(sequenced[1]['plain'])
    fact = symbol['loc_measurement_evidence']
    observed = None
    if mutation == 'source': fact['atlas_source_hash'] = '0' * 32
    elif mutation == 'version': fact['ast_contract_version'] = 'old'
    elif mutation == 'unit': fact['offset_unit'] = 'utf16_code_units'
    elif mutation == 'kind': fact['callable_kind'] = []
    elif mutation == 'extra': fact['compatible'] = True
    elif mutation == 'line': fact['spans']['declaration']['end_line'] += 1
    elif mutation == 'observed': observed = 999
    else: symbol.pop('loc_measurement_evidence')
    finding = _finding(_policy(tmp_path), symbol, observed=observed)
    assert finding['limit'] == 150 and finding['limit_authority'] == 'sage_default'


@pytest.mark.parametrize('mutation', ['hash', 'line', 'offset', 'version', 'unit'])
def test_raw_record_validation_uses_actual_text_before_coordinate_conversion(sequenced, mutation):
    raw = deepcopy(next(row for row in sequenced[0] if row['name'] == 'plain'))
    record = raw['loc_measurement_evidence']
    if mutation == 'hash': record['source_hash'] = '0' * 64
    elif mutation == 'line': record['spans']['callable']['line'] += 1
    elif mutation == 'offset': record['spans']['callable']['end'] = len(SOURCE) + 1000
    elif mutation == 'version': record['version'] = 'old'
    else: record['offset_unit'] = 'utf8_bytes'
    result = _normalize_polyglot_symbols([raw], SOURCE, 'typescript')[0]
    assert result['loc_measurement_evidence']['status'] == 'unavailable'


def test_normalization_reuses_file_hash_and_unicode_coordinate_context(sequenced):
    from tools.engines import generate_atlas as atlas
    with patch.object(atlas, '_atlas_text_hash', wraps=atlas._atlas_text_hash) as digest:
        normalized = atlas._normalize_polyglot_symbols(sequenced[0], SOURCE, 'typescript')
    assert digest.call_count == 1
    plain = next(row for row in normalized if row['name'] == 'plain')
    call = plain['loc_measurement_evidence']['spans']['callable']
    assert SOURCE.encode('utf-8')[call['start']:call['end']].decode('utf-8').startswith('function plain()')


def test_mcp_existing_buffer_facts_cannot_be_borrowed_for_another_source(tmp_path, sequenced):
    from tools.core import pipeline_policy
    raw = sequenced[0]
    with patch.dict(pipeline_policy.DYNAMIC_CONFIG, {'effective_target_policy': _policy(tmp_path)}):
        bound = mcp._existing_loc_violation_keys(raw, project='MAIN', file_path='probe.ts', source_content=SOURCE)
        foreign = mcp._existing_loc_violation_keys(raw, project='MAIN', file_path='probe.ts', source_content=SOURCE + '// changed\n')
    assert ('loc_limits_symbol', 'plain') in bound
    assert ('loc_limits_symbol', 'plain') not in foreign


def test_source_fact_schema_and_current_policy_schema_are_valid(sequenced):
    import jsonschema
    schema = json.loads((ROOT/'config/schemas/atlas.schema.json').read_text(encoding='utf-8'))
    fact = sequenced[1]['plain']['loc_measurement_evidence']
    jsonschema.Draft202012Validator({'$ref': '#/$defs/loc_measurement_evidence', '$defs': schema['$defs']}).validate(fact)
    policy_schema = json.loads((ROOT/'config/schemas/target_policy_profile_contract.schema.json').read_text(encoding='utf-8'))
    jsonschema.validate(json.loads((ROOT/'config/target_policy_profile_contract.json').read_text(encoding='utf-8')), policy_schema)


@pytest.mark.parametrize('damage', ['positive', 'unavailable', 'unit', 'flag_type', 'span_extra', 'hash', 'missing'])
def test_native_sage_schema_validator_enforces_the_same_measurement_variant(sequenced, damage):
    from tools.core.artifact_validator import _validate
    import jsonschema
    schema = json.loads((ROOT/'config/schemas/atlas.schema.json').read_text(encoding='utf-8'))
    fact = deepcopy(sequenced[1]['plain']['loc_measurement_evidence'])
    if damage == 'unavailable': fact = {'status': 'unavailable', 'reason': 'invalid_source'}
    elif damage == 'unit': fact['offset_unit'] = 'utf16_code_units'
    elif damage == 'flag_type': fact['non_iife'] = 1
    elif damage == 'span_extra': fact['spans']['unproved'] = fact['spans']['callable']
    elif damage == 'hash': fact['source_hash'] = '?'
    elif damage == 'missing': fact.pop('spans')
    errors = []
    _validate(fact, schema['$defs']['loc_measurement_evidence'], schema, ['loc_measurement'], errors)
    expected_valid = damage in {'positive', 'unavailable'}
    assert (not errors) is expected_valid
    validator = jsonschema.Draft202012Validator({'$ref': '#/$defs/loc_measurement_evidence', '$defs': schema['$defs']})
    assert validator.is_valid(fact) is expected_valid


def test_actual_mcp_proposed_and_existing_buffers_use_the_same_admission(tmp_path, sequenced, monkeypatch):
    from tools.core import pipeline_policy
    target = tmp_path/'probe.ts'
    target.write_text('export function plain() { return 0; }\n', encoding='utf-8')
    monkeypatch.setattr(mcp, 'RAW_DIR', tmp_path/'raw')
    monkeypatch.setattr(mcp, 'resolve_runtime_projects', lambda *_: {'MAIN': tmp_path})
    monkeypatch.setattr(mcp, 'load_effective_architecture_policy_context', lambda *_: ({}, ''))
    with patch.dict(pipeline_policy.DYNAMIC_CONFIG, {'effective_target_policy': _policy(tmp_path)}):
        result = mcp.validate_proposed_patch('probe.ts', SOURCE, workspace_root=tmp_path)
    finding = next(row for row in result['violations'] if row.get('symbol_name') == 'plain')
    assert finding['limit'] == 2 and finding['observed_loc'] == 5
    assert finding['target_policy_resolution']['measurement_compatibility'][0]['source_hash'] == sequenced[2]


def test_shared_ast_normalization_bytes_participate_in_staging_identity(monkeypatch):
    from tools.engines.generate_atlas import atlas_staging_producer_contract
    before = atlas_staging_producer_contract()
    original = Path.read_bytes
    def changed(path):
        value = original(path)
        return value + b'\n# different shared normalizer' if path.name == 'atlas_ast_contract.py' else value
    monkeypatch.setattr(Path, 'read_bytes', changed)
    assert atlas_staging_producer_contract() != before


def test_unsupported_callable_forms_and_parser_degradation_do_not_emit_measurement_facts(tmp_path):
    path = tmp_path/'unsupported.ts'
    source = ('export async function asyncFn() { return 1; }\n'
        'export function* generator() { yield 1; }\n'
        'export function typed(value: number) { return value; }\n'
        'export function returns(): number { return 1; }\n'
        'export const wrapper = (() => 1);\n'
        'export let mutable = () => 1;\n'
        'export class Container { method() { return 1; } }\n')
    path.write_text(source, encoding='utf-8')
    rows = mcp.run_ast_sequencer(path, '.ts')
    assert rows and all('loc_measurement_evidence' not in row for row in rows)
    path.write_text('export function broken( { return 1; }', encoding='utf-8')
    partial = mcp.run_ast_sequencer(path, '.ts')
    assert all('loc_measurement_evidence' not in row for row in partial)


@pytest.mark.parametrize('flags', [{'IIFEs': True}, {'IIFEs': False}, {'skipBlankLines': False, 'skipComments': False}])
def test_all_line_options_keep_non_iife_source_control(tmp_path, sequenced, flags):
    assert _finding(_policy(tmp_path, flags=flags), sequenced[1]['plain'])['limit'] == 2


def test_biome_stays_a_distinct_measurement_even_with_valid_source_facts(tmp_path, sequenced):
    finding = _finding(_policy(tmp_path, 'biome', version='2.2.7'), sequenced[1]['plain'])
    assert finding['limit'] == 150 and finding['observed_loc'] == 5
    assert finding['target_policy_resolution']['unresolved_reasons'] == ['native_measurement_basis_not_supported']


def test_actual_audit_sqlite_and_agent_projection_preserve_one_measurement_decision(tmp_path, sequenced, monkeypatch):
    from tools.core import pipeline_policy, contextos_mcp
    from tools.core.artifact_store import ArtifactStore
    from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION
    from tools.core.atlas_integrity import build_atlas_commit
    from tools.core.atlas_io import load_atlas_data_with_identity
    from tools.core.db import SQLiteManager
    from tools.engines import audit
    from tools.engines.generate_atlas import _atlas_text_hash
    from tools.mcp import server
    raw = tmp_path/'.raw'; raw.mkdir()
    (tmp_path/'probe.ts').write_bytes(SOURCE.encode('utf-8'))
    meta = {'hash': _atlas_text_hash(SOURCE), 'size': len(SOURCE.encode('utf-8')), 'loc': 10,
        'ast_contract_version': AST_CONTRACT_VERSION, 'parser_evidence': {'status': 'observed'},
        'symbols': [sequenced[1]['plain']], 'features': [], 'imports': [], 'language': 'typescript'}
    atlas = {'MAIN': {'root_path': str(tmp_path), 'project_type': 'typescript',
        'files': {'probe.ts': meta}, 'dependencies': {}, 'symbols': []}}
    store = ArtifactStore(raw_dir=raw); store.use_sqlite = True; store.backend = 'hybrid_sqlite'
    store.db_manager = SQLiteManager(raw/'codemaps.db'); store._schema_initialized = False; store._ensure_schema()
    store._save_payload_to_state_table('atlas', atlas); store._save_atlas_to_sqlite(atlas)
    store._save_payload_to_state_table('atlas_commit', build_atlas_commit(atlas))
    monkeypatch.setattr(audit, 'RAW_DIR', raw)
    monkeypatch.setattr(audit, 'resolve_runtime_projects', lambda *_: {'MAIN': tmp_path})
    monkeypatch.setattr(audit, 'watchdog_artifact_identity', lambda: {
        **audit._canonical_audit_artifact_identity(atlas), 'artifact_root': str(raw),
        'target_descriptor': {'target_root': str(tmp_path), 'fixture_scope_only': True}})
    monkeypatch.setattr(audit, 'watchdog_artifact_path', lambda *_: raw/'watchdog_audit.json')
    monkeypatch.setattr(audit, '_load_structural_contract_health', lambda: {
        'status': 'not_available_in_audit_phase', 'reason': 'isolated static LOC fixture',
        **{key: 0.0 for key in ['atlas_contract_file_ratio', 'member_detail_contract_ratio',
            'genome_occurrence_contract_ratio', 'atlas_current_version_ratio', 'genome_current_version_ratio']}})
    reports = []
    monkeypatch.setattr(audit, 'save_json_atomic', lambda _path, payload: reports.append(deepcopy(payload)))
    with patch.dict(pipeline_policy.DYNAMIC_CONFIG, {'effective_target_policy': _policy(tmp_path)}):
        assert audit.analyze_project(changed_files=['MAIN::probe.ts'], atlas=atlas)
    report = reports[-1]
    finding = next(row for row in report['violations'] if row.get('symbol_name') == 'plain')
    assert finding['limit'] == 2 and finding['observed_loc'] == 5
    store._save_audit_findings_to_sqlite(report)
    items, total, available = server._audit_violation_work_items_from_sqlite(raw, page=1, page_size=10, project='MAIN')
    assert available and total >= 1
    work_item = next(row for row in items if row['rule'] == finding['rule'])
    directive = contextos_mcp._directive_for_violation(finding, report, index=0,
        atlas_read=load_atlas_data_with_identity(raw), raw_dir=raw)
    for projected in [work_item['finding_semantics'], directive['finding_semantics']]:
        assert projected['target_policy_resolution'] == finding['target_policy_resolution']
        assert projected['limit_authority'] == finding['limit_authority']

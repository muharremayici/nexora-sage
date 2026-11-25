"""Verify collector-owned checked text before issuing Atlas lineage.

Syntax observations cover listed files, not project type safety. Semantic
collection records bounded compiler/config/declaration observations, not snapshot
authority, and remains unavailable to snapshot-based scoring until bound.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
import hashlib
import json
import ntpath
import os
import re
from typing import Any
from contextvars import ContextVar
from dataclasses import dataclass, field

from tools.core.atlas_typescript_inputs import (
    POLICY_PATH as INPUT_POLICY_PATH, auxiliary_input_policy, resolution_input_policy, valid_directory_name,
)
from tools.core.json_io import load_json_object_strict, load_json_object_strict_cached
from tools.core.source_snapshot_integrity import snapshot_hash_algorithms


# A private projection may supply an immutable policy view for this call only.
# Default production callers still use the existing content-fresh policy owners.
_SEMANTIC_POLICY_VIEW = ContextVar('typescript_semantic_policy_view', default=None)
_SEMANTIC_OWNER_TOKEN = object()


def _semantic_metadata_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _semantic_metadata_sha(value):
    return hashlib.sha256(_semantic_metadata_json(value).encode('utf-8')).hexdigest()


_LIVE_CONFIG_OWNER_TOKEN = object()


@dataclass(frozen=True, eq=False)
class _LiveConfigContext:
    metadata_json: str = field(repr=False)
    token: object = field(repr=False)


def hold_live_config_context(*, atlas_commit, parent_validation_sha256, project, source_sha256,
                             config_binding, observation_sha256, policy_inputs, schemas):
    """Copy private owner references, never raw Atlas or collector assertions.

    The caller owns actual canonical parent validation and observation origin.
    Commit/schema/digest agreement is declared correspondence, not proof that
    validation ran, a current live capture, or hostile-caller authentication.
    """
    from tools.core.artifact_validator import _validate
    try:
        settings = policy_inputs['lineage']['artifacts']['ts_diagnostics']
        policy = settings['private_live_config_context']; schema = schemas['view']
        lane = settings['semantic_input_gaps']['config_extends']
        if (set(policy_inputs) != {'lineage', 'algorithms'} or set(schemas) != {'view', 'config', 'commit'}
                or _semantic_metadata_sha(schema) != policy['schema_sha256']
                or _semantic_metadata_sha(schemas['commit']) != policy['commit_schema_sha256']
                or _semantic_metadata_sha(schemas['config']) != lane['observation_schema_sha256']
                or lane['profile'] != policy['config_profile']):
            raise ValueError
        expected = {'project': project, 'source_sha256': source_sha256,
            'parent_validation_sha256': parent_validation_sha256, 'observation_sha256': observation_sha256}
        errors = []
        _validate(policy, schema, schema, ['live_config_policy'], errors)
        _validate(expected, schema['$defs']['expected'], schema, ['live_config_owner'], errors)
        _validate(atlas_commit, schemas['commit'], schemas['commit'], ['live_config_commit'], errors)
        if errors or any(policy[k] is not False for k in ('default_enabled', 'native_execution_enabled',
                'production_enabled', 'semantic_lineage', 'cache_eligible')):
            raise ValueError
        config = settings[policy['config_profile']]; limits = settings[config['config_limit_profile']]
        metadata_bytes = settings[limits['image_profile']]['limits']['metadata_bytes']
        detail_limit = settings['semantic_context']['max_observations']
        id_limit = settings[limits['bridge_profile']]['max_request_id_chars']
        algorithms = policy_inputs['algorithms']; source_policy = lane['source_records']
        if (any(type(n) is not int or n < 1 for n in (metadata_bytes, detail_limit, id_limit))
                or len(project) > id_limit or project not in atlas_commit['projects']
                or any(not _config_digest(atlas_commit.get(k)) for k in ('snapshot_id', 'atlas_sha256'))
                or not isinstance(algorithms, dict) or not algorithms
                or any(not isinstance(v, str) or str(hashlib.new(v).digest_size * 2) != k for k, v in algorithms.items())
                or source_policy['version'] != 'v1-config-declared-root-atlas-record'
                or source_policy['basis'] != 'held_atlas_record_only'
                or source_policy['success_status'] != 'CONFIG_DECLARED_ROOT_ATLAS_RECORD_OBSERVED_NOT_BOUND'
                or source_policy['unknown_status'] != 'UNKNOWN_CONFIG_DECLARED_ROOT_ATLAS_RECORD'
                or any(source_policy[k] is not False for k in ('default_enabled', 'production_enabled', 'semantic_lineage', 'cache_eligible'))
                or config['declared_source_name'] != limits['atlas_relative_path']
                or config['project_virtual_root'] + '/' + config['declared_source_name'] != limits['compiler_virtual_path']):
            raise ValueError
        commit_json = _semantic_metadata_json(atlas_commit)
        if len(commit_json.encode('utf-8')) > metadata_bytes:
            raise ValueError
        policies = {**policy_inputs, 'config_extends_schema': schemas['config']}
        held = {'parent': {k: atlas_commit[k] for k in ('snapshot_id', 'atlas_sha256')},
            'projects': [project], 'policies': policies, 'expected': expected,
            'parent_commit_sha256': hashlib.sha256(commit_json.encode('utf-8')).hexdigest(),
            'config_extends_inputs': {project: {'binding': config_binding, 'observation_sha256': observation_sha256}},
            'config_source_records': source_policy['version'], 'metadata_bytes': metadata_bytes, 'schema': schema}
        _hold_config_extends_references(held, held['config_extends_inputs'])
        encoded = _semantic_metadata_json(held)
        if len(encoded.encode('utf-8')) > metadata_bytes:
            raise ValueError
        return _LiveConfigContext(encoded, _LIVE_CONFIG_OWNER_TOKEN)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise ValueError('live_config_policy_or_owner_refused') from None


def project_live_config_context(atlas, observation, *, expected_context):
    """Compare one held parent/config/source context through unchanged pure leaves.

    No collector input, target/policy reads, canonical commit rebuild, parser or
    compiler. Full Atlas is used only during this call and never enters output.
    Serialization ceilings do not attest peak memory, atomic capture or origin.
    """
    out = {'version': 'v1-private-live-config-context', 'status': 'UNKNOWN_LIVE_CONFIG_CONTEXT',
        'basis': 'held_atlas_config_correspondence_only', 'code': 'live_config_owner_unavailable',
        'parent_snapshot_id': None, 'atlas_sha256': None, 'parent_commit_sha256': None,
        'parent_validation_sha256': None, 'project_sha256': None, 'config_observation_sha256': None,
        'config_binding_sha256': None, 'source_sha256': None, 'config_matched': False,
        'source_record_matched': False, 'config_code': '', 'source_code': '',
        'source_records': {'shown': 0, 'omitted': 0},
        'semantic_gap_view': {'status': 'UNAVAILABLE_NO_COLLECTOR_INPUT', 'gap_count': None},
        **{k: False for k in ('snapshot_bound', 'production_enabled', 'semantic_lineage',
            'cache_eligible', 'native_project_equivalent', 'loaded_code_attested')}}
    if not isinstance(expected_context, _LiveConfigContext) or expected_context.token is not _LIVE_CONFIG_OWNER_TOKEN:
        return out
    token = None
    held = json.loads(expected_context.metadata_json); expected = held['expected']
    policy = held['policies']['lineage']['artifacts']['ts_diagnostics']['private_live_config_context']
    out.update(version=policy['version'], status=policy['unknown_status'], code='',
        parent_snapshot_id=held['parent']['snapshot_id'], atlas_sha256=held['parent']['atlas_sha256'],
        parent_commit_sha256=held['parent_commit_sha256'], parent_validation_sha256=expected['parent_validation_sha256'],
        project_sha256=hashlib.sha256(expected['project'].encode('utf-8')).hexdigest())
    try:
        atlas_bytes = _semantic_metadata_json(atlas).encode('utf-8')
        observation_bytes = _semantic_metadata_json(observation).encode('utf-8')
        if len(atlas_bytes) + len(observation_bytes) > held['metadata_bytes']:
            raise ValueError('live_config_budget_exceeded')
        if not isinstance(atlas, dict) or hashlib.sha256(atlas_bytes).hexdigest() != held['parent']['atlas_sha256']:
            raise ValueError('live_config_parent_mismatch')
        # Both leaves consume the exact checked call-local bytes, never mutable
        # caller objects that may change after hashing. No source/config reparse.
        atlas, observation = json.loads(atlas_bytes), json.loads(observation_bytes)
        project = expected['project']
        if not isinstance(atlas.get(project), dict):
            raise ValueError('live_config_project_unavailable')
        token = _SEMANTIC_POLICY_VIEW.set(held['policies'])
        sidecar = _project_config_extends_inputs(held, {project: observation}, atlas=atlas)
        if sidecar['context_code'] or sidecar['shown'] != 1 or sidecar['omitted']:
            raise ValueError('live_config_observation_unavailable')
        row = sidecar['details'][0]
        out.update(config_code=row['code'], source_records=sidecar['declared_source_records'])
        if row['status'] != 'CONFIG_SOURCE_SELECTION_OBSERVED_NOT_BOUND':
            raise ValueError('live_config_observation_unavailable')
        out.update(config_matched=True, config_observation_sha256=row['observation_sha256'], config_binding_sha256=row['binding_sha256'])
        source = row.get('declared_source_record')
        if source is None:
            raise ValueError('live_config_source_unavailable')
        out['source_code'] = source['code']
        selected = held['policies']['lineage']['artifacts']['ts_diagnostics']['semantic_input_gaps']['config_extends']['source_records']
        if source['status'] != selected['success_status']:
            raise ValueError('live_config_source_unavailable')
        if source['atlas_content_identity'] != {'algorithm': 'sha256', 'value': expected['source_sha256']}:
            raise ValueError('live_config_source_mismatch')
        out.update(status=policy['success_status'], source_record_matched=True, source_sha256=expected['source_sha256'])
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError) as error:
        code = str(error)
        out['code'] = code if code in policy['refusal_codes'] else 'live_config_metadata_invalid'
    finally:
        if token is not None:
            _SEMANTIC_POLICY_VIEW.reset(token)
    from tools.core.artifact_validator import _validate
    errors = []; schema = held['schema']
    _validate(out, schema['$defs']['result'], schema, ['live_config_result'], errors)
    if errors or len(_semantic_metadata_json(out).encode('utf-8')) > held['metadata_bytes']:
        raise ValueError('live_config_result_unavailable')
    return out


_NATIVE_RECEIPT_OWNER_TOKEN = object()


@dataclass(frozen=True, eq=False)
class _NativeReceiptContext:
    metadata_json: str = field(repr=False)
    token: object = field(repr=False)


def hold_native_receipt_context(*, expected, policy_inputs, schemas):
    """Hold independently validated retained-receipt references, never execution authority.

    No files are read. The owner supplies canonical policy/schema data and receipt
    origin. An opaque token and matching digests do not authenticate hostile callers.
    Only bounded expected metadata/schema copies live here, not a native payload.
    """
    from tools.core.artifact_validator import _validate
    try:
        settings = policy_inputs['lineage']['artifacts']['ts_diagnostics']
        policy = settings['private_native_receipt_view']; schema = schemas['view']
        if set(schemas) != {'view', 'config', 'preparation'}:
            raise ValueError
        if (_semantic_metadata_sha(schema) != policy['schema_sha256']
                or _semantic_metadata_sha(schemas['preparation']) != policy['preparation_schema_sha256']
                or _semantic_metadata_sha(schemas['config']) != settings['semantic_input_gaps']['config_extends']['observation_schema_sha256']):
            raise ValueError
        errors = []
        _validate(policy, schema, schema, ['receipt_policy'], errors)
        _validate(expected, schema['$defs']['expected'], schema, ['receipt_owner'], errors)
        config = settings[policy['config_profile']]
        preparation = settings[policy['preparation_profile']]
        _validate(preparation, schemas['preparation'], schemas['preparation'], ['preparation_policy'], errors)
        if errors or any(policy[k] is not False for k in ('default_enabled', 'native_execution_enabled',
                'production_enabled', 'semantic_lineage', 'cache_eligible')):
            raise ValueError
        limits = settings[config['config_limit_profile']]
        metadata_bytes = settings[limits['image_profile']]['limits']['metadata_bytes']
        detail_limit = settings['semantic_context']['max_observations']
        id_limit = settings[limits['bridge_profile']]['max_request_id_chars']
        host_limit = settings[limits['image_profile']]['limits']['host_calls']
        if (any(type(n) is not int or n < 1 for n in (metadata_bytes, detail_limit, id_limit, host_limit))
                or len(expected['external_attempt_id']) > id_limit
                or settings['semantic_input_gaps']['config_extends']['profile'] != policy['config_profile']
                or any(config[k] is not False for k in ('default_enabled', 'production_enabled', 'semantic_lineage', 'cache_eligible'))):
            raise ValueError
        held = {'expected': expected, 'policy': policy, 'schemas': schemas,
            'metadata_bytes': metadata_bytes, 'detail_limit': detail_limit, 'id_limit': id_limit,
            'config_policy_sha256': _semantic_metadata_sha(config), 'compiler_version': config['compiler_version'],
            'config_host_limit': host_limit,
            'config_path_hashes': {role: hashlib.sha256(row['virtual_path'].encode('utf-8')).hexdigest() for role, row in config['roster'].items()},
            'config_specifier_sha256': hashlib.sha256(config['extends_literal'].encode('utf-8')).hexdigest(),
            'config_declared_root_sha256': hashlib.sha256((config['project_virtual_root'] + '/' + config['declared_source_name']).encode('utf-8')).hexdigest(),
            'phase_order': preparation['phase_order'], 'counter_limits': preparation['counter_limits']}
        encoded = _semantic_metadata_json(held)
        if len(encoded.encode('utf-8')) > metadata_bytes:
            raise ValueError
        return _NativeReceiptContext(encoded, _NATIVE_RECEIPT_OWNER_TOKEN)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise ValueError('native_receipt_policy_or_owner_refused') from None


def project_native_receipt_availability(receipt, *, expected_context):
    """Private pure metadata companion; no canonical semantic/config gap replay.

    A match describes retained declarations. Full Atlas/binding/source rows and an
    actual collector are unavailable in this route. It never reconstructs them.
    """
    flags = {k: False for k in ('snapshot_bound', 'production_enabled', 'semantic_lineage',
        'cache_eligible', 'native_project_equivalent', 'loaded_code_attested')}
    out = {'version': 'v1-private-retained-native-receipt-view', 'status': 'UNKNOWN_RETAINED_NATIVE_RECEIPT',
        'authority': 'declared_correspondence_only', 'basis': 'held_retained_receipt_metadata_only',
        'code': 'native_receipt_owner_unavailable', 'external_attempt_sha256': None, 'receipt_sha256': None,
        'source_reference_sha256': None, 'parent_snapshot_id': None, 'atlas_sha256': None,
        'observed_components': {'details': [], 'shown': 0, 'omitted': 0},
        'missing_inputs': {'status': 'UNKNOWN_UNAVAILABLE', 'details': [], 'shown': 0, 'omitted': 0},
        'reported_costs': None, 'cost_basis': 'outer_source_api_and_reported_config_counts_only',
        'semantic_gap_view': {'status': 'UNKNOWN_UNAVAILABLE', 'gap_count': None},
        'config_sidecar_replayed': False, 'source_record_correspondence': 'UNKNOWN_NOT_HELD', **flags}
    if not isinstance(expected_context, _NativeReceiptContext) or expected_context.token is not _NATIVE_RECEIPT_OWNER_TOKEN:
        return out
    held = json.loads(expected_context.metadata_json); expected = held['expected']; policy = held['policy']
    out.update(version=policy['version'], status=policy['unknown_status'], code='')
    try:
        def require(ok, code):
            if not ok:
                raise ValueError(code)
        encoded = _semantic_metadata_json(receipt).encode('utf-8')
        require(len(encoded) <= held['metadata_bytes'], 'native_receipt_budget_exceeded')
        require(hashlib.sha256(encoded).hexdigest() == expected['receipt_sha256'], 'native_receipt_digest_mismatch')
        from tools.core.artifact_validator import _validate
        schemas = held['schemas']; schema = schemas['view']; errors = []
        _validate(receipt, schema['$defs']['input'], schema, ['retained_receipt'], errors)
        require(not errors, 'native_receipt_schema_invalid')
        native = receipt['native']; parser = native['parser']; parent = native['parent']; config = native['config_observation']
        _validate(config, schemas['config'], schemas['config'], ['retained_config'], errors)
        _validate(native['preparation_projection'], schemas['preparation']['$defs']['result'], schemas['preparation'], ['retained_preparation'], errors)
        require(not errors, 'native_receipt_schema_invalid')
        require(receipt['external_attempt_id'] == expected['external_attempt_id']
            and len(receipt['external_attempt_id']) <= held['id_limit']
            and receipt['source_reference_sha256'] == expected['source_reference_sha256']
            and native['capture']['source_sha256'] == parser['source_sha256'] == expected['source_sha256']
            and parser['parser_response_sha256'] == expected['parser_response_sha256']
            and parent['parent_snapshot_id'] == config['parent_snapshot_id'] == expected['parent_snapshot_id']
            and parent['atlas_sha256'] == config['atlas_sha256'] == expected['atlas_sha256']
            and parent['validation_receipt_sha256'] == expected['parent_validation_sha256']
            and native['config_binding_sha256'] == config['binding_sha256'] == expected['config_binding_sha256']
            and native['compiler']['source_sha256'] == config['compiler_source_sha256'] == expected['compiler_source_sha256']
            and native['capture']['auxiliary_context_sha256'] == config['context_sha256'] == expected['auxiliary_context_sha256']
            and native['compiler']['version'] == held['compiler_version']
            and config['policy_sha256'] == held['config_policy_sha256'], 'native_receipt_identity_mismatch')
        counters = parser['counters']; context = counters['context']
        require(context['compiler_source_sha256'] == expected['compiler_source_sha256']
            and all(len(context[k]) <= held['id_limit'] for k in ('invocation_id', 'attempt_id')),
            'native_receipt_identity_mismatch')
        for name, limit in held['counter_limits'].items():
            row = counters['counts'][name]
            require(row['attempted'] == row['forwarded'] == row['completed'] <= limit
                and row['thrown'] == row['denied'] == 0, 'native_receipt_components_incomplete')
        require(counters['counts']['createSourceFile']['completed'] == 1, 'native_receipt_components_incomplete')
        prep = native['preparation_projection']
        require(prep['status'] == 'JOIN_PREPARATION_METADATA_CONFORMANT_NOT_EXECUTED'
            and prep['accepted_phases'] == held['phase_order'] and prep['next_phase'] is None and prep['code'] == ''
            and config['status'] == 'PRIVATE_CONFIG_EXTENDS_CORRESPONDENCE_NOT_BOUND' and config['code'] == ''
            and [config[k] for k in ('root_parse_calls', 'config_chain_calls', 'config_sourcefiles', 'cache_entries')] == [1, 1, 2, 1]
            and config['programs'] == config['source_worker_parses'] == config['denied_host_calls'] == 0
            and config['reads'] == {'root': 1, 'base': 1} and not config['diagnostic_codes'] and config['host_refusal'] == '',
            'native_receipt_components_incomplete')
        strict = config['strict_observation']; edge = config['edge']
        require(1 <= config['host_calls'] <= held['config_host_limit']
            and strict['effective'] is (strict['base'] if strict['root_override'] is None else strict['root_override'])
            and edge['source_path_sha256'] == held['config_path_hashes']['root']
            and edge['target_path_sha256'] == held['config_path_hashes']['base']
            and edge['specifier_sha256'] == held['config_specifier_sha256']
            and config['declared_root_sha256'] == held['config_declared_root_sha256'],
            'native_receipt_components_incomplete')
        summaries = {'source_parser': parser, 'frozen_parent': parent, 'config_chain': config}
        components = out['observed_components']; missing = out['missing_inputs']; remaining = held['metadata_bytes']
        for facet in policy['component_order']:
            row = {'facet': facet, 'basis': 'retained_declared_observation_only', 'detail_sha256': _semantic_metadata_sha(summaries[facet])}
            charge = len(_semantic_metadata_json(row).encode('utf-8')) + 1
            if len(components['details']) < held['detail_limit'] and charge <= remaining:
                components['details'].append(row); remaining -= charge
            else:
                components['omitted'] += 1
        for item in policy['missing_inputs']:
            charge = len(_semantic_metadata_json(item).encode('utf-8')) + 1
            if len(missing['details']) < held['detail_limit'] and charge <= remaining:
                missing['details'].append(item); remaining -= charge
            else:
                missing['omitted'] += 1
        components['shown'] = len(components['details']); missing['shown'] = len(missing['details'])
        missing['status'] = 'KNOWN_UNAVAILABLE_IN_RETAINED_SUMMARY'
        out.update(status=policy['success_status'], code='', external_attempt_sha256=hashlib.sha256(receipt['external_attempt_id'].encode('utf-8')).hexdigest(),
            receipt_sha256=expected['receipt_sha256'], source_reference_sha256=expected['source_reference_sha256'],
            parent_snapshot_id=expected['parent_snapshot_id'], atlas_sha256=expected['atlas_sha256'],
            reported_costs={'source_worker_requests': native['source_worker_requests'],
                'source_outer_createSourceFile': counters['counts']['createSourceFile']['completed'],
                'source_outer_createProgram': counters['counts']['createProgram']['completed'],
                'worker_restarts': parser['transport']['worker_restarts'], 'request_replays': parser['transport']['request_replays'],
                **{k: config[k] for k in ('config_chain_calls', 'config_sourcefiles')}, 'config_programs': config['programs']},
            semantic_gap_view={'status': 'UNAVAILABLE_COLLECTOR_NOT_EXECUTED', 'gap_count': None})
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError) as error:
        code = str(error)
        out['code'] = code if code in policy['refusal_codes'] else 'native_receipt_metadata_invalid'
    return out


def format_native_receipt_summary(view, *, expected_context=None):
    """Private presentation of an existing view; never rerun or certify its origin.

    Use the same separate owner context and in-memory schema. Curated labels and
    finite protocol counts keep text short; no raw child strings/errors are echoed.
    The caller owns the exact view/result transport, not hostile-code attestation.
    """
    unavailable = ('Retained receipt summary unavailable (metadata only).\n'
        'Semantic gaps: unknown; semantic/cache/production authority: unavailable.')
    if not isinstance(expected_context, _NativeReceiptContext) or expected_context.token is not _NATIVE_RECEIPT_OWNER_TOKEN:
        return unavailable
    try:
        held = json.loads(expected_context.metadata_json); policy = held['policy']; expected = held['expected']
        if len(_semantic_metadata_json(view).encode('utf-8')) > held['metadata_bytes']:
            return unavailable
        from tools.core.artifact_validator import _validate
        schema = held['schemas']['view']; errors = []
        _validate(view, schema['$defs']['result'], schema, ['receipt_view'], errors)
        if errors or view['status'] != policy['success_status'] or view['code'] != '':
            return unavailable
        if (any(view[k] != expected[k] for k in ('receipt_sha256', 'source_reference_sha256', 'parent_snapshot_id', 'atlas_sha256'))
                or view['external_attempt_sha256'] != hashlib.sha256(expected['external_attempt_id'].encode('utf-8')).hexdigest()
                or view['missing_inputs']['status'] != 'KNOWN_UNAVAILABLE_IN_RETAINED_SUMMARY'
                or view['semantic_gap_view'] != {'status': 'UNAVAILABLE_COLLECTOR_NOT_EXECUTED', 'gap_count': None}):
            return unavailable
        components = view['observed_components']; missing = view['missing_inputs']
        facets = [row['facet'] for row in components['details']]
        for shown, allowed, group in ((facets, policy['component_order'], components),
                (missing['details'], policy['missing_inputs'], missing)):
            if (len(shown) > held['detail_limit'] or shown != [item for item in allowed if item in shown]
                    or group['shown'] != len(shown) or group['omitted'] != len(allowed) - len(shown)):
                return unavailable
        costs = view['reported_costs']
        if not isinstance(costs, dict):
            return unavailable
        # The shared schema walker has no anyOf branch. Validate the existing
        # cost definition directly rather than relying on its nullable wrapper.
        _validate(costs, schema['$defs']['costs'], schema, ['reported_costs'], errors)
        if (errors or costs['source_worker_requests'] != 1
                or costs['source_outer_createSourceFile'] != 1
                or not 0 <= costs['source_outer_createProgram'] <= held['counter_limits']['createProgram']
                or costs['worker_restarts'] != 0 or costs['request_replays'] != 0
                or costs['config_chain_calls'] != 1 or costs['config_sourcefiles'] != len(held['config_path_hashes'])
                or costs['config_programs'] != 0):
            return unavailable
        # Presentation labels only; codes, limits and admission remain in their owners.
        labels = {'source_parser': 'source parser', 'frozen_parent': 'frozen Atlas', 'config_chain': 'config chain',
            'full_validated_atlas': 'full validated Atlas', 'full_config_binding': 'complete config binding',
            'held_atlas_source_record': 'held Atlas source record', 'semantic_collector_result': 'semantic collector result',
            'compiler_input_inventory': 'compiler input inventory'}
        observed_text = ', '.join(labels[item] for item in facets) if facets else 'none shown'
        missing_text = ', '.join(labels[item] for item in missing['details']) if missing['details'] else 'none shown'
        return '\n'.join([
            'Retained native receipt ' + view['receipt_sha256'][:12] + ' (historical metadata only).',
            f"Observations: shown={components['shown']}, omitted={components['omitted']}; unavailable inputs: shown={missing['shown']}, omitted={missing['omitted']}.",
            'Shown observations: ' + observed_text + '.',
            'Shown unavailable inputs: ' + missing_text + '.',
            f"Reported source work: requests={costs['source_worker_requests']}, outer SourceFiles={costs['source_outer_createSourceFile']}, outer Programs={costs['source_outer_createProgram']}, restarts={costs['worker_restarts']}, replays={costs['request_replays']}.",
            f"Reported config work: chains={costs['config_chain_calls']}, SourceFiles={costs['config_sourcefiles']}, Programs={costs['config_programs']}.",
            'Semantic gaps: unknown (collector not executed); semantic/cache/production authority: unavailable.'])
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        return unavailable


@dataclass(frozen=True, eq=False)
class _SemanticInputContext:
    metadata_json: str = field(repr=False)
    token: object = field(repr=False)


def hold_semantic_input_context(*, atlas_commit, collector_run_id, projects, compiler_inputs, policy_inputs,
                                config_extends_inputs=None, config_source_records=False):
    """Hold separately supplied owner metadata, not a child's context assertion.

    The caller owns canonical parent/policy validation and inventory origin.
    This value binds their declared bytes; it authenticates no arbitrary caller,
    loaded code, atomic snapshot or fresh execution. Nothing is read or persisted.
    """
    policy = policy_inputs['lineage']['artifacts']['ts_diagnostics']['semantic_input_gaps']
    limit = policy_inputs['lineage']['artifacts']['ts_diagnostics']['semantic_context']['max_observations']
    if (policy.get('version') != 'v1-semantic-input-gaps'
            or any(policy.get(k) is not False for k in ('default_enabled', 'production_enabled', 'semantic_lineage', 'cache_eligible'))
            or policy.get('detail_limit_from') != 'semantic_context.max_observations'
            or [policy.get(k) for k in ('unknown_status', 'gaps_status', 'reconciled_status')] != [
                'UNKNOWN_INPUT_CONTEXT', 'INPUT_GAPS_PRESENT_NOT_BOUND', 'OBSERVATIONS_RECONCILED_NOT_BOUND']
            or any(not isinstance(c, str) or not re.fullmatch('semantic_input_[a-z_]+', c)
                for c in policy.get('context_refusal_codes', []))
            or type(limit) is not int or limit < 1
            or not isinstance(atlas_commit, dict) or atlas_commit.get('state') != 'complete'
            or any(not isinstance(atlas_commit.get(k), str) or not re.fullmatch('[a-f0-9]{64}', atlas_commit[k]) for k in ('snapshot_id', 'atlas_sha256'))
            or not isinstance(collector_run_id, str) or not collector_run_id
            or not isinstance(projects, (list, tuple)) or not projects or len(projects) > limit
            or any(not isinstance(p, str) or not p for p in projects) or len(set(projects)) != len(projects)
            or not isinstance(compiler_inputs, dict)):
        raise ValueError('semantic_input_owner_context_invalid')
    algorithms = policy_inputs['algorithms']
    if (not isinstance(algorithms, dict) or not algorithms
            or any(str(hashlib.new(v).digest_size * 2) != k for k, v in algorithms.items())):
        raise ValueError('semantic_input_owner_policy_invalid')
    metadata = {'parent': {k: atlas_commit[k] for k in ('snapshot_id', 'atlas_sha256')},
        'collector_run_id': collector_run_id, 'projects': sorted(projects), 'compiler_inputs': compiler_inputs,
        'policies': policy_inputs}
    if config_extends_inputs is not None:
        _hold_config_extends_references(metadata, config_extends_inputs)
        metadata['config_extends_inputs'] = config_extends_inputs
    if type(config_source_records) is not bool:
        raise ValueError('semantic_config_source_records_owner_invalid')
    if config_source_records:
        settings = policy_inputs['lineage']['artifacts']['ts_diagnostics']
        lane = settings['semantic_input_gaps']['config_extends']
        selected = lane['source_records']; config_policy = settings[lane['profile']]
        source_policy = settings[config_policy['config_limit_profile']]
        metadata_limit = settings[source_policy['image_profile']]['limits']['metadata_bytes']
        if (config_extends_inputs is None or selected['version'] != 'v1-config-declared-root-atlas-record'
                or any(selected[k] is not False for k in ('default_enabled', 'production_enabled', 'semantic_lineage', 'cache_eligible'))
                or selected['success_status'] != 'CONFIG_DECLARED_ROOT_ATLAS_RECORD_OBSERVED_NOT_BOUND'
                or selected['unknown_status'] != 'UNKNOWN_CONFIG_DECLARED_ROOT_ATLAS_RECORD'
                or selected['basis'] != 'held_atlas_record_only'
                or type(metadata_limit) is not int or metadata_limit < 1
                or config_policy['declared_source_name'] != source_policy['atlas_relative_path']
                or config_policy['project_virtual_root'] + '/' + config_policy['declared_source_name'] != source_policy['compiler_virtual_path']):
            raise ValueError('semantic_config_source_records_owner_invalid')
        metadata['config_source_records'] = selected['version']
    return _SemanticInputContext(_semantic_metadata_json(metadata), _SEMANTIC_OWNER_TOKEN)


def _hold_config_extends_references(held, references):
    """Owner transport references, supplied separately from the child response.

    The owner must validate their origin and canonical schema. Holding an exact
    observation digest prevents a response from updating its own expected facts;
    it does not authenticate a hostile in-process caller or native execution.
    """
    settings = held['policies']['lineage']['artifacts']['ts_diagnostics']
    lane = settings['semantic_input_gaps']['config_extends']
    policy = settings[lane['profile']]
    schema = held['policies']['config_extends_schema']
    config = settings[policy['config_limit_profile']]
    if (lane['version'] != 'v1-config-extends-gap-observation'
            or any(lane[k] is not False for k in ('default_enabled', 'production_enabled', 'semantic_lineage', 'cache_eligible'))
            or any(policy[k] is not False for k in ('default_enabled', 'production_enabled', 'semantic_lineage', 'cache_eligible'))
            or not isinstance(lane.get('refusal_codes'), list)
            or any(not isinstance(code, str) or not re.fullmatch('config_extends_[a-z_]+', code) for code in lane['refusal_codes'])
            or _semantic_metadata_sha(schema) != lane['observation_schema_sha256']
            or not isinstance(references, dict) or not references or not set(references) <= set(held['projects'])):
        raise ValueError('semantic_config_owner_invalid')
    input_policy = {'config': config, 'image': settings[config['image_profile']],
        'bridge': settings[config['bridge_profile']], 'auxiliary': settings['atlas_auxiliary_inputs']}
    for row in references.values():
        binding = row.get('binding') if isinstance(row, dict) else None
        if (not isinstance(row, dict) or set(row) != {'binding', 'observation_sha256'}
                or not _config_digest(row['observation_sha256']) or not isinstance(binding, dict)
                or set(binding) != {'atlas_snapshot_id', 'atlas_sha256', 'invocation_id', 'compiler_version',
                    'compiler_source_sha256', 'policy_sha256', 'auxiliary_policy_sha256', 'input_policy_sha256', 'context_sha256', 'files'}
                or binding.get('atlas_snapshot_id') != held['parent']['snapshot_id']
                or binding.get('atlas_sha256') != held['parent']['atlas_sha256']
                or binding.get('compiler_version') != policy['compiler_version']
                or not _config_digest(binding.get('compiler_source_sha256'))
                or not _config_digest(binding.get('context_sha256'))
                or not isinstance(binding.get('invocation_id'), str)
                or not re.fullmatch('[A-Za-z0-9_-]+', binding['invocation_id'])
                or len(binding['invocation_id']) > settings[config['bridge_profile']]['max_request_id_chars']
                or binding.get('policy_sha256') != _semantic_metadata_sha(policy)
                or binding.get('auxiliary_policy_sha256') != _semantic_metadata_sha(settings['atlas_auxiliary_inputs'])
                or binding.get('input_policy_sha256') != _semantic_metadata_sha(input_policy)
                or not isinstance(binding.get('files'), dict) or set(binding['files']) != set(policy['roster'])):
            raise ValueError('semantic_config_owner_invalid')
        for role, expected in policy['roster'].items():
            source = binding['files'][role]
            if (not isinstance(source, dict) or set(source) != {'atlas_path', 'virtual_path', 'bytes', 'raw_sha256', 'host_sha256'}
                    or any(source[k] != expected[k] for k in ('atlas_path', 'virtual_path'))
                    or type(source['bytes']) is not int or not 0 <= source['bytes'] <= config['max_config_bytes']
                    or not _config_digest(source['raw_sha256']) or not _config_digest(source['host_sha256'])):
                raise ValueError('semantic_config_owner_invalid')


def _config_digest(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def _config_declared_source_record(held, project, atlas, *, config_matched):
    """An exact held Atlas record, never a live source or compiler observation."""
    settings = held['policies']['lineage']['artifacts']['ts_diagnostics']
    lane = settings['semantic_input_gaps']['config_extends']
    policy = lane['source_records']; config = settings[lane['profile']]
    result = {'status': policy['unknown_status'], 'basis': policy['basis'],
        'code': 'config_declared_root_config_unavailable', 'declared_virtual_path_sha256': None,
        'atlas_relative_path_sha256': None, 'atlas_content_identity': {'algorithm': None, 'value': None}}
    if not config_matched:
        return result
    relative = config['declared_source_name']
    result.update(declared_virtual_path_sha256=hashlib.sha256((config['project_virtual_root']+'/'+relative).encode('utf-8')).hexdigest(),
        atlas_relative_path_sha256=hashlib.sha256(relative.encode('utf-8')).hexdigest())
    project_atlas = atlas.get(project) if isinstance(atlas, dict) else None
    files = project_atlas.get('files') if isinstance(project_atlas, dict) else None
    if not isinstance(files, dict):
        result['code'] = 'config_declared_root_inventory_unavailable'
        return result
    source = files.get(relative)
    if (not isinstance(source, dict) or not isinstance(source.get('language'), str)
            or not _captured_canonical_source_file(relative, project_atlas)):
        result['code'] = 'config_declared_root_record_unavailable'
        return result
    identity = source['hash']
    result.update(status=policy['success_status'], code='',
        atlas_content_identity={'algorithm': _binding_algorithms()[str(len(identity))], 'value': identity})
    return result


def _project_config_extends_inputs(held, observations, *, atlas=None):
    """Bounded held metadata only; canonical gap/rejection results stay intact."""
    result = {'context_code': '', 'details': [], 'shown': 0, 'omitted': 0}
    references = held.get('config_extends_inputs')
    if not references:
        result['context_code'] = 'config_extends_owner_unavailable'
        return result
    settings = held['policies']['lineage']['artifacts']['ts_diagnostics']
    lane = settings['semantic_input_gaps']['config_extends']; policy = settings[lane['profile']]
    limit = settings['semantic_context']['max_observations']
    cap = settings[settings[lane['profile']]['config_limit_profile']]['max_request_bytes']
    if observations is None:
        observations = {}
    if not isinstance(observations, dict) or not set(observations) <= set(references) or len(observations) > limit:
        result['context_code'] = 'config_extends_scope_mismatch'
        return result
    source_budget = 0
    if 'config_source_records' in held:
        result['declared_source_records'] = {'shown': 0, 'omitted': 0}
        source_budget = settings[settings[policy['config_limit_profile']]['image_profile']]['limits']['metadata_bytes']
    # Reuse the existing in-memory schema walker, without a schema/policy read.
    from tools.core.artifact_validator import _validate
    for project, reference in sorted(references.items()):
        row = {'project_sha256': hashlib.sha256(project.encode('utf-8')).hexdigest(),
            'status': 'UNKNOWN_CONFIG_EXTENDS_INPUT', 'code': 'config_extends_observation_missing',
            'observation_sha256': None, 'binding_sha256': None}
        observation = observations.get(project)
        if observation is not None:
            try:
                encoded = _semantic_metadata_json(observation).encode('utf-8')
                if len(encoded) > cap:
                    raise ValueError('config_extends_metadata_budget_exceeded')
                identity = hashlib.sha256(encoded).hexdigest()
                if identity != reference['observation_sha256']:
                    raise ValueError('config_extends_observation_mismatch')
                errors = []; schema = held['policies']['config_extends_schema']
                _validate(observation, schema, schema, ['config_extends'], errors)
                if errors:
                    raise ValueError('config_extends_observation_invalid')
                expected = reference['binding']; edge = observation['edge']
                if (observation['binding_sha256'] != _semantic_metadata_sha(expected)
                        or any(observation[k] != expected[v] for k, v in (
                            ('parent_snapshot_id', 'atlas_snapshot_id'), ('atlas_sha256', 'atlas_sha256'),
                            ('compiler_source_sha256', 'compiler_source_sha256'), ('context_sha256', 'context_sha256'),
                            ('policy_sha256', 'policy_sha256')))):
                    raise ValueError('config_extends_context_mismatch')
                if (observation['status'] != policy['success_status'] or observation['code']
                        or observation['diagnostic_codes'] or observation['host_refusal'] or observation['denied_host_calls']
                        or [observation[k] for k in ('root_parse_calls', 'config_chain_calls', 'config_sourcefiles', 'cache_entries')] != [1, 1, 2, 1]
                        or observation['reads'] != {'root': 1, 'base': 1}
                        or not 1 <= observation['host_calls'] <= settings[settings[policy['config_limit_profile']]['image_profile']]['limits']['host_calls']):
                    raise ValueError('config_extends_observation_incomplete')
                for role, prefix in (('root', 'source'), ('base', 'target')):
                    source = expected['files'][role]
                    if (edge[prefix + '_role'] != role
                            or edge[prefix + '_raw_sha256'] != source['raw_sha256']
                            or edge[prefix + '_host_sha256'] != source['host_sha256']
                            or edge[prefix + '_path_sha256'] != hashlib.sha256(source['virtual_path'].encode('utf-8')).hexdigest()):
                        raise ValueError('config_extends_source_mismatch')
                span = edge['literal_host_span_utf16']; strict = observation['strict_observation']
                if (type(span['start']) is not int or type(span['end']) is not int or not 0 <= span['start'] < span['end'] <= expected['files']['root']['bytes']
                        or type(edge['line']) is not int or type(edge['character']) is not int
                        or edge['specifier_sha256'] != hashlib.sha256(policy['extends_literal'].encode('utf-8')).hexdigest()
                        or observation['declared_root_sha256'] != hashlib.sha256((policy['project_virtual_root'] + '/' + policy['declared_source_name']).encode('utf-8')).hexdigest()
                        or type(strict['base']) is not bool or type(strict['effective']) is not bool
                        or strict['root_override'] is not None and type(strict['root_override']) is not bool
                        or strict['effective'] is not (strict['base'] if strict['root_override'] is None else strict['root_override'])):
                    raise ValueError('config_extends_source_mismatch')
                row.update(status='CONFIG_SOURCE_SELECTION_OBSERVED_NOT_BOUND', code='',
                    observation_sha256=identity, binding_sha256=observation['binding_sha256'])
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
                code = str(error)
                row['code'] = code if code in lane['refusal_codes'] else 'config_extends_observation_invalid'
        if 'config_source_records' in held:
            source_record = _config_declared_source_record(held, project, atlas,
                config_matched=row['status'] == 'CONFIG_SOURCE_SELECTION_OBSERVED_NOT_BOUND')
            charge = len(_semantic_metadata_json({'declared_source_record': source_record}).encode('utf-8')) + 1
            if charge <= source_budget:
                row['declared_source_record'] = source_record
                result['declared_source_records']['shown'] += 1
                source_budget -= charge
            else:
                result['declared_source_records']['omitted'] += 1
        if len(result['details']) < limit:
            result['details'].append(row)
        else:
            result['omitted'] += 1
    result['shown'] = len(result['details'])
    return result


def project_semantic_input_gaps(payload, atlas, *, expected_context, config_extends_observations=None):
    """Read-only opt-in correspondence summary; never issues semantic lineage.

    Reuse held policy data in the same canonical validators. The ContextVar is
    restored even on refusal; another task/default caller receives no override.
    Output contains canonical code families and detail hashes, not target paths,
    source text or full diagnostic messages. A matching context is not admission.
    """
    flags = {'snapshot_bound': False, 'production_enabled': False, 'semantic_lineage': False,
        'cache_eligible': False, 'native_project_equivalent': False, 'loaded_code_attested': False}
    if not isinstance(expected_context, _SemanticInputContext) or expected_context.token is not _SEMANTIC_OWNER_TOKEN:
        return {'version': 'v1-semantic-input-gaps', 'status': 'UNKNOWN_INPUT_CONTEXT', 'authority': 'observation_only',
            'context_code': 'semantic_input_owner_unavailable', 'parent_snapshot_id': None, 'atlas_sha256': None,
            'collector_run_sha256': None, 'policy_sha256': None, 'details': [], 'shown': 0, 'omitted': 0,
            'gap_count': 0, 'authority_blocker_count': 0, **flags}
    held = json.loads(expected_context.metadata_json)
    policies = held['policies']; settings = policies['lineage']['artifacts']['ts_diagnostics']
    policy = settings['semantic_input_gaps']; limit = settings['semantic_context']['max_observations']
    output = {'version': policy['version'], 'status': policy['unknown_status'], 'authority': 'observation_only',
        'context_code': '', 'parent_snapshot_id': held['parent']['snapshot_id'], 'atlas_sha256': held['parent']['atlas_sha256'],
        'collector_run_sha256': hashlib.sha256(held['collector_run_id'].encode('utf-8')).hexdigest(),
        'policy_sha256': _semantic_metadata_sha(policies), 'details': [], 'shown': 0, 'omitted': 0,
        'gap_count': 0, 'authority_blocker_count': 0, **flags}
    token = None
    try:
        if not isinstance(atlas, dict) or _semantic_metadata_sha(atlas) != held['parent']['atlas_sha256']:
            raise ValueError('semantic_input_parent_mismatch')
        meta = payload.get('meta') if isinstance(payload, dict) else None
        scope = meta.get('execution_scope') if isinstance(meta, dict) else None
        projects = payload.get('projects') if isinstance(payload, dict) else None
        if (not isinstance(meta, dict) or meta.get('collector_run_id') != held['collector_run_id']
                or payload.get('collector_status') != 'OK'):
            raise ValueError('semantic_input_invocation_mismatch')
        if (not isinstance(projects, dict) or sorted(projects) != held['projects'] or not isinstance(scope, dict)
                or any(not isinstance(scope.get(k), list) or sorted(scope[k]) != held['projects'] for k in ('requested_projects', 'analyzed_projects'))
                or scope.get('unavailable_requested_projects') or any(p not in atlas for p in projects)):
            raise ValueError('semantic_input_project_scope_mismatch')
        units = 0
        for data in projects.values():
            context = data.get('semantic_context') if isinstance(data, dict) else None
            rows = context.get('observations') if isinstance(context, dict) else None
            if not isinstance(rows, list) or data.get('mode') != 'semantic' or data.get('status') != 'OK':
                raise ValueError('semantic_input_observations_unavailable')
            units += len(rows)
            for row in rows:
                trace = row.get('recursive_inputs') if isinstance(row, dict) else None
                if trace is not None:
                    if not isinstance(trace, dict) or not isinstance(trace.get('observations'), list):
                        raise ValueError('semantic_input_observations_unavailable')
                    units += len(trace['observations'])
            if units > limit:
                raise ValueError('semantic_input_observation_budget_exceeded')
        token = _SEMANTIC_POLICY_VIEW.set(policies)

        def record(project, facet, issue):
            code = issue.split(':', 1)[0]
            authority = code in policy['authority_blocker_codes']
            output['authority_blocker_count' if authority else 'gap_count'] += 1
            if len(output['details']) < limit:
                output['details'].append({'project_sha256': hashlib.sha256(project.encode('utf-8')).hexdigest() if project is not None else None, 'facet': 'authority' if authority else facet,
                    'code': code, 'detail_sha256': hashlib.sha256(issue.encode('utf-8')).hexdigest()})
            else:
                output['omitted'] += 1

        for project, data in projects.items():
            issues = []
            _append_semantic_errors(issues, '', data, atlas, project)
            for issue in issues:
                record(project, 'semantic_inputs', issue)
            if data.get('diagnostics_truncated') is not False:
                record(project, 'semantic_inputs', 'semantic_diagnostic_coverage_unavailable')
        inventory = held['compiler_inputs']
        if (inventory.get('authority') != 'producer_preinvocation_installation_capture'
                or inventory.get('collector_run_id') != held['collector_run_id']):
            record(None, 'compiler_inputs', 'compiler_library_owner_unavailable')
        else:
            library = reconcile_compiler_library_inputs(payload, inventory)
            for issue in library.get('errors', []):
                # Library reconciliation prefixes project-specific errors.
                project = next((p for p in sorted(projects, key=len, reverse=True) if issue.startswith(p + ':')), None)
                record(project, 'compiler_inputs', issue[len(project) + 1:] if project is not None else issue)
        output['shown'] = len(output['details'])
        output['status'] = policy['gaps_status'] if output['gap_count'] else policy['reconciled_status']
        if config_extends_observations is not None or 'config_extends_inputs' in held:
            output['config_extends'] = _project_config_extends_inputs(held, config_extends_observations, atlas=atlas)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        known = str(error)
        output.update(status=policy['unknown_status'], context_code=known if known in policy['context_refusal_codes'] else 'semantic_input_metadata_invalid',
            details=[], shown=0, omitted=0, gap_count=0, authority_blocker_count=0)
    finally:
        if token is not None:
            _SEMANTIC_POLICY_VIEW.reset(token)
    return output


def _binding_lineage_policy():
    held = _SEMANTIC_POLICY_VIEW.get()
    return held['lineage'] if held is not None else load_json_object_strict_cached(INPUT_POLICY_PATH, label="Analysis snapshot lineage")


def _binding_algorithms():
    held = _SEMANTIC_POLICY_VIEW.get()
    return held['algorithms'] if held is not None else snapshot_hash_algorithms()


def _binding_resolution_policy():
    held = _SEMANTIC_POLICY_VIEW.get()
    return held['lineage']['artifacts']['ts_diagnostics']['atlas_resolution_inputs'] if held is not None else resolution_input_policy()


def _binding_auxiliary_policy():
    held = _SEMANTIC_POLICY_VIEW.get()
    return held['lineage']['artifacts']['ts_diagnostics']['atlas_auxiliary_inputs'] if held is not None else auxiliary_input_policy()


def _atlas_source_hash_matches(captured: Any, observed: Any) -> bool:
    """Compare actual parsed text using the centrally allowed Atlas digest width."""
    if not isinstance(captured, str) or not isinstance(observed, dict):
        return False
    algorithm = _binding_algorithms().get(str(len(captured)))
    value = observed.get(algorithm) if algorithm in {"md5", "sha256"} else None
    return bool(isinstance(value, str) and re.fullmatch(r"[0-9a-f]+", captured)
                and isinstance(value, str) and re.fullmatch(r"[0-9a-f]+", value)
                and len(value) == len(captured) and value == captured)


def _valid_atlas_source_hash(captured: Any) -> bool:
    if not isinstance(captured, str):
        return False
    algorithm = _binding_algorithms().get(str(len(captured)))
    return bool(algorithm in {"md5", "sha256"} and re.fullmatch(r"[0-9a-f]+", captured))


_COMPILER_LIBRARY_CAPSULE_OWNER = object()


class _CompilerLibraryCapsule:
    """Private invocation-local bytes, not a wire object or an authentication token.

    Disposal drops this owner's references; it cannot revoke bytes already
    returned to a consumer or guarantee erasure of Python/OS memory copies.
    """

    __slots__ = ('_metadata_json', '_package', '_libraries')

    def __init__(self, owner, metadata, package, libraries):
        if owner is not _COMPILER_LIBRARY_CAPSULE_OWNER:
            raise TypeError('compiler_library_capsule_owner_required')
        self._metadata_json = json.dumps(metadata, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
        self._package = package
        self._libraries = tuple(libraries.items())

    def __repr__(self):
        return '<_CompilerLibraryCapsule disposed>' if self._metadata_json is None else '<_CompilerLibraryCapsule live>'

    def __reduce_ex__(self, protocol):
        raise TypeError('compiler_library_capsule_not_serializable')

    def _require_live(self):
        if self._metadata_json is None:
            raise ValueError('compiler_library_capsule_disposed')

    def metadata(self):
        self._require_live()
        return json.loads(self._metadata_json)

    def package_bytes(self):
        self._require_live()
        return self._package

    def library_bytes(self):
        self._require_live()
        return dict(self._libraries)

    def dispose(self):
        self._metadata_json = self._package = None
        self._libraries = ()

    def __enter__(self):
        self._require_live()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.dispose()


def _plan_owned_compiler_library_transport(capsule, *, expected_inventory, base_frame,
                                         profiles, schema, include_package=False):
    """Size an unimplemented canonical JSON bundle without encoding raw inputs.

    The base descriptor declares an already serialized JSONL object's byte
    count, digest and source/config lengths; its content/origin is not verified.
    The prospective bundle is {base: <that object>, compiler: <owned inputs>}.
    A size fit never admits its shape, complete image, freshness or authority.
    The caller still owns the capsule and its disposal.
    """
    from tools.core.artifact_validator import _validate
    constraints = {key: None for key in ('base_wire', 'source_raw', 'config_raw', 'library_count',
        'library_file_raw', 'image_compiler_count', 'image_compiler_raw', 'package_metadata_raw',
        'image_content_lower_bound', 'draft_wire')}
    result = {'version': 'v1-private-compiler-transport-size', 'status': 'COMPILER_TRANSPORT_PLAN_UNAVAILABLE',
        'code': '', 'context_sha256': None, 'inventory_sha256': None, 'declared_base_sha256': None,
        'draft_wire_bytes': None, 'wire_limit_bytes': None, 'compiler_files': None, 'compiler_raw_bytes': None,
        'package_included': None, 'image_content_lower_bound_bytes': None, 'constraints': constraints,
        'whole_image_within_limit': None, 'peak_rss_bytes': None, 'source_freshness_verified': False,
        'current_reader_supported': False, 'base_content_verified': False,
        'raw_frame_emitted': False, 'snapshot_bound': False, 'semantic_lineage': False,
        'production_enabled': False, 'cache_eligible': False, 'loaded_code_attested': False}
    code = 'compiler_plan_policy_unavailable'
    try:
        descriptor = profiles['private_compiler_transport_plan']
        config = profiles[descriptor['wire_profile']]
        image = profiles[descriptor['image_profile']]['limits']
        capture = profiles[descriptor['capture_profile']]
        package_policy = profiles[descriptor['package_profile']]
        bridge = profiles[config['bridge_profile']]
        ceiling = image['metadata_bytes']
        if type(ceiling) is not int or ceiling < 1:
            raise ValueError
        # Check call-local JSON copies, never caller-mutating policy/base views.
        text = _semantic_metadata_json({'profiles': profiles, 'schema': schema,
            'base': base_frame, 'expected': expected_inventory})
        code = 'compiler_plan_metadata_over_budget'
        if len(text.encode('utf-8')) > ceiling:
            raise ValueError
        held = json.loads(text)
        profiles, schema, base_frame, expected_inventory = (held[k] for k in ('profiles', 'schema', 'base', 'expected'))
        descriptor = profiles['private_compiler_transport_plan']
        code = 'compiler_plan_policy_unavailable'
        errors = []
        _validate(descriptor, schema, schema, ['compiler_plan_policy'], errors)
        _validate(base_frame, schema['$defs']['base'], schema, ['compiler_plan_base'], errors)
        if (errors or descriptor['schema_sha256'] != _semantic_metadata_sha(schema) or type(include_package) is not bool
                or descriptor['version'] != result['version'] or descriptor['model'] != 'canonical_json_base_and_compiler_v1'
                or any(descriptor[k] is not False for k in ('default_enabled', 'native_execution_enabled',
                    'raw_transport_enabled', 'production_enabled', 'semantic_lineage', 'cache_eligible'))):
            raise ValueError
        config, image, capture, package_policy = (profiles[descriptor[k]] for k in
            ('wire_profile', 'image_profile', 'capture_profile', 'package_profile'))
        image = image['limits']
        bridge = profiles[config['bridge_profile']]
        values = [config['max_request_bytes'], config['max_config_bytes'], bridge['max_source_bytes'],
            capture['max_files'], capture['max_file_bytes'], image['compiler_files'], image['compiler_bytes'],
            image['compiler_file_bytes'], image['image_bytes'], image['metadata_bytes'], package_policy['max_metadata_bytes']]
        if any(type(n) is not int or n < 1 for n in values):
            raise ValueError
        code = 'compiler_plan_owner_unavailable'
        if type(capsule) is not _CompilerLibraryCapsule:
            raise ValueError
        metadata = capsule.metadata()
        libraries, package = capsule.library_bytes(), capsule.package_bytes()
        code = 'compiler_plan_identity_mismatch'
        if (_semantic_metadata_json(metadata) != _semantic_metadata_json(expected_inventory)
                or metadata['status'] != 'captured' or metadata['snapshot_bound'] is not False
                or metadata['loaded_code_attested'] is not False
                or not _config_digest(metadata['inventory_sha256'])
                or not _config_digest(metadata['installed_module_sha256'])
                or not isinstance(metadata['collector_run_id'], str) or not metadata['collector_run_id']
                or set(libraries) != set(metadata['files']) or not libraries):
            raise ValueError
        code = 'compiler_plan_owned_bytes_mismatch'
        rows, sizes = [], []
        for name, raw in sorted(libraries.items()):
            entry = metadata['files'][name]
            if (not isinstance(name, str) or type(raw) is not bytes
                    or hashlib.sha256(raw).hexdigest() != entry['sha256']
                    or not _config_digest(entry['host_text_sha256'])):
                raise ValueError
            sizes.append(len(raw))
            rows.append({'name': name, 'input': {'bytes': len(raw), 'raw_sha256': entry['sha256'],
                'host_text_sha256': entry['host_text_sha256'], 'utf8Base64': ''}})
        if type(package) is not bytes or hashlib.sha256(package).hexdigest() != metadata['package_sha256']:
            raise ValueError
        package_wire = None
        if include_package:
            # Raw package identity is already owned. Host hash is not invented:
            # the prospective package row carries only the raw bytes reference.
            package_wire = {'bytes': len(package), 'raw_sha256': metadata['package_sha256'], 'utf8Base64': ''}
        compiler = {'inventory_sha256': metadata['inventory_sha256'],
            'installed_module_sha256': metadata['installed_module_sha256'],
            'collector_run_id': metadata['collector_run_id'], 'libraries': rows, 'package': package_wire}
        encoded_sizes = sizes + ([len(package)] if include_package else [])
        compiler_json = _semantic_metadata_json(compiler).encode('utf-8')
        # Base64 is ASCII without JSON escaping. Count each file's padding and
        # the final newline. No raw bytes or base frame text are serialized.
        required = (len(b'{"base":') + base_frame['jsonl_bytes'] - 1 + len(b',"compiler":')
            + len(compiler_json) + sum(4 * ((n + 2) // 3) for n in encoded_sizes) + len(b'}\n'))
        compiler_files = len(libraries) + int(include_package)
        compiler_bytes = sum(encoded_sizes)
        lower_bound = base_frame['source_bytes'] + sum(base_frame['config_bytes'].values()) + compiler_bytes
        constraints.update(base_wire=base_frame['jsonl_bytes'] <= config['max_request_bytes'],
            source_raw=base_frame['source_bytes'] <= bridge['max_source_bytes'],
            config_raw=all(n <= config['max_config_bytes'] for n in base_frame['config_bytes'].values()),
            library_count=len(libraries) <= capture['max_files'],
            library_file_raw=all(n <= min(capture['max_file_bytes'], image['compiler_file_bytes']) for n in sizes),
            image_compiler_count=compiler_files <= image['compiler_files'],
            image_compiler_raw=compiler_bytes <= image['compiler_bytes'],
            package_metadata_raw=not include_package or len(package) <= package_policy['max_metadata_bytes'],
            image_content_lower_bound=lower_bound <= image['image_bytes'], draft_wire=required <= config['max_request_bytes'])
        result.update(status=descriptor['budget_status'] if False in constraints.values() else descriptor['size_status'],
            code='', context_sha256=_semantic_metadata_sha({'base': base_frame, 'inventory': metadata,
                'profiles': profiles, 'include_package': include_package}), inventory_sha256=metadata['inventory_sha256'],
            declared_base_sha256=base_frame['sha256'], draft_wire_bytes=required,
            wire_limit_bytes=config['max_request_bytes'], compiler_files=compiler_files, compiler_raw_bytes=compiler_bytes,
            package_included=include_package, image_content_lower_bound_bytes=lower_bound)
    except (KeyError, TypeError, ValueError, UnicodeError):
        # This private advisory view reports a fixed stage code, never raw
        # input, decoder text, filesystem error or a positive availability claim.
        result['code'] = code
    return result


def _compiler_stream_frame_fields(kind, frame_context, manifest_sha256, seq, *,
                                  records=(), manifest=None, files=0, raw_bytes=0,
                                  transcript_sha256=None):
    """Single private field-format owner, shared by counting and actual encoding."""
    frame = {'version': frame_context['version'], 'session_id': frame_context['session_id'],
        'manifest_sha256': manifest_sha256, 'seq': seq, 'kind': kind}
    if kind == 'data':
        frame['records'] = records
    elif kind == 'open':
        frame['manifest'] = manifest
    elif kind == 'commit':
        frame.update(files_complete=files, raw_bytes_total=raw_bytes, transcript_sha256=transcript_sha256)
    else:
        raise ValueError('stream_frame_kind_unavailable')
    return frame


def _compiler_stream_jsonl_size(kind, frame_context, manifest_sha256, seq, *,
                                records=(), manifest=None, files=0, raw_bytes=0):
    """Count exact JSONL bytes without raw encoding; scaffolding never escapes."""
    rows = [{**row, 'chunk_sha256': '0' * 64, 'utf8Base64': ''} for row in records]
    frame = _compiler_stream_frame_fields(kind, frame_context, manifest_sha256, seq,
        records=rows, manifest=manifest, files=files, raw_bytes=raw_bytes, transcript_sha256='0' * 64)
    return len(_semantic_metadata_json(frame).encode('utf-8')) + 1 + (
        sum(4 * ((row['raw_bytes'] + 2) // 3) for row in records) if kind == 'data' else 0)


def _compiler_stream_data_layout(files, frame_context, wire_limit, metadata_limit, deadline):
    """Greedy contiguous ranges with bounded metadata and progress, no raw bytes."""
    import time
    frames, records, seq, recipe_bytes = [], [], 1, 0

    def check_time():
        if time.monotonic() >= deadline:
            raise TimeoutError('stream_layout_time_budget')

    def size(rows):
        return _compiler_stream_jsonl_size('data', frame_context, '0' * 64, seq, records=rows)

    def flush():
        nonlocal records, seq, recipe_bytes
        row = {'seq': seq, 'jsonl_bytes': size(records), 'records': records}
        recipe_bytes += len(_semantic_metadata_json(row).encode('utf-8')) + 1
        if recipe_bytes > metadata_limit:
            return False
        frames.append(row)
        records, seq = [], seq + 1
        return True

    for index, file in enumerate(files):
        offset, length = 0, file['bytes']
        # Empty files retain their manifest slot/hash, with no empty data record.
        while offset < length:
            check_time()
            def candidate(count):
                return {'file_index': index, 'offset': offset, 'raw_bytes': count}
            if size(records + [candidate(1)]) > wire_limit:
                if not records:
                    return None, 'data_wire'
                if not flush():
                    return None, 'manifest_metadata'
                continue
            low, high = 1, length - offset
            while low < high:
                check_time()
                middle = (low + high + 1) // 2
                if size(records + [candidate(middle)]) <= wire_limit:
                    low = middle
                else:
                    high = middle - 1
            records.append(candidate(low))
            offset += low
            if offset < length and not flush():
                return None, 'manifest_metadata'
    if records and not flush():
        return None, 'manifest_metadata'
    return frames, None


def _plan_owned_compiler_stream_layout(capsule, *, expected_inventory, context, profiles, schema):
    """Private declarations-only metadata layout; no IO, base64, sender or admission.

    The caller supplies independent inventory/context and keeps the capsule live.
    Counts/hashes bind metadata, not hostile callers or a transmitted byte stream.
    """
    import time
    from tools.core.artifact_validator import _validate
    started = time.monotonic()
    constraints = {key: None for key in ('library_count', 'library_file_raw',
        'capture_library_raw_lower_bound', 'image_compiler_count', 'image_compiler_raw',
        'image_compiler_file_raw', 'image_content_lower_bound', 'data_wire', 'open_wire',
        'commit_wire', 'manifest_metadata', 'result_metadata')}
    empty = {'version': 'v1-private-compiler-stream-layout', 'status': 'COMPILER_STREAM_LAYOUT_UNAVAILABLE',
        'code': '', 'budget_reason': None, 'context_sha256': None, 'inventory_sha256': None, 'manifest_sha256': None,
        'manifest': None, 'frames': None, 'library_files': None, 'library_raw_bytes': None,
        'frame_limit_bytes': None, 'wire_bytes_total': None, 'largest_frame_jsonl_bytes': None,
        'manifest_json_bytes': None, 'record_count': None, 'chunked_files': None,
        'constraints': constraints, 'package_included': False, 'whole_image_within_limit': None,
        'peak_rss_bytes': None, 'current_reader_supported': False, 'raw_frame_emitted': False,
        'raw_content_verified': False, 'base_content_verified': False, 'send_verified': False,
        'source_freshness_verified': False, 'snapshot_bound': False, 'loaded_code_attested': False,
        'native_execution_attested': False, 'production_enabled': False, 'semantic_lineage': False,
        'cache_eligible': False}
    code = 'stream_plan_policy_unavailable'
    try:
        descriptor = profiles['private_compiler_stream_layout']
        ceiling = profiles[descriptor['image_profile']]['limits']['metadata_bytes']
        if type(ceiling) is not int or ceiling < 1:
            raise ValueError
        text = _semantic_metadata_json({'profiles': profiles, 'schema': schema,
            'inventory': expected_inventory, 'context': context})
        code = 'stream_plan_metadata_over_budget'
        if len(text.encode('utf-8')) > ceiling:
            raise ValueError
        held = json.loads(text)
        profiles, schema, expected_inventory, context = (held[k] for k in ('profiles', 'schema', 'inventory', 'context'))
        descriptor = profiles['private_compiler_stream_layout']
        code = 'stream_plan_policy_unavailable'
        errors = []
        _validate(descriptor, schema['$defs']['stream_policy'], schema, ['stream_policy'], errors)
        if errors or descriptor['schema_sha256'] != _semantic_metadata_sha(schema):
            raise ValueError
        wire = profiles[descriptor['wire_profile']]['max_request_bytes']
        bridge = profiles[profiles[descriptor['wire_profile']]['bridge_profile']]
        image = profiles[descriptor['image_profile']]['limits']
        capture = profiles[descriptor['capture_profile']]
        values = [wire, image['compiler_files'], image['compiler_bytes'], image['compiler_file_bytes'],
            image['image_bytes'], image['metadata_bytes'], image['capture_ms'], capture['max_files'],
            capture['max_file_bytes'], capture['max_total_bytes'], bridge['max_request_id_chars']]
        if any(type(n) is not int or n < 1 for n in values):
            raise ValueError
        code = 'stream_plan_context_unavailable'
        _validate(context, schema['$defs']['stream_context'], schema, ['stream_context'], errors)
        if errors or len(context['session_id']) > bridge['max_request_id_chars']:
            raise ValueError
        code = 'stream_plan_owner_unavailable'
        if (type(capsule) is not _CompilerLibraryCapsule
                or not isinstance(getattr(capsule, '_metadata_json', None), str)
                or type(getattr(capsule, '_libraries', None)) is not tuple):
            raise ValueError
        metadata, libraries = capsule.metadata(), capsule.library_bytes()
        code = 'stream_plan_inventory_mismatch'
        if (_semantic_metadata_json(metadata) != _semantic_metadata_json(expected_inventory)
                or metadata['status'] != 'captured' or metadata['snapshot_bound'] is not False
                or metadata['loaded_code_attested'] is not False or not libraries
                or set(libraries) != set(metadata['files'])
                or not _config_digest(metadata['inventory_sha256'])
                or not _config_digest(metadata['installed_module_sha256'])
                or not isinstance(metadata['compiler_version'], str) or not metadata['compiler_version']
                or not isinstance(metadata['collector_run_id'], str) or not metadata['collector_run_id']):
            raise ValueError
        code = 'stream_plan_owned_bytes_mismatch'
        files, sizes, folded = [], [], set()
        for name, raw in sorted(libraries.items()):
            entry = metadata['files'][name]
            if (not isinstance(name, str) or re.fullmatch(r'lib[A-Za-z0-9_.-]*\.d\.ts', name) is None
                    or name.lower() in folded or type(raw) is not bytes
                    or hashlib.sha256(raw).hexdigest() != entry['sha256']
                    or not _config_digest(entry['host_text_sha256'])):
                raise ValueError
            folded.add(name.lower()); sizes.append(len(raw))
            files.append({'name': name, 'bytes': len(raw), 'raw_sha256': entry['sha256'],
                'host_text_sha256': entry['host_text_sha256']})
        count, raw_total = len(files), sum(sizes)
        constraints.update(library_count=count <= capture['max_files'],
            library_file_raw=all(n <= capture['max_file_bytes'] for n in sizes),
            capture_library_raw_lower_bound=raw_total <= capture['max_total_bytes'],
            image_compiler_count=count <= image['compiler_files'], image_compiler_raw=raw_total <= image['compiler_bytes'],
            image_compiler_file_raw=all(n <= image['compiler_file_bytes'] for n in sizes),
            image_content_lower_bound=raw_total <= image['image_bytes'])
        context_sha = _semantic_metadata_sha({'context': context, 'inventory': metadata, 'profiles': profiles})
        result = {**empty, 'constraints': dict(constraints), 'code': '', 'context_sha256': context_sha,
            'inventory_sha256': metadata['inventory_sha256'], 'library_files': count, 'library_raw_bytes': raw_total,
            'frame_limit_bytes': wire}
        constraints = result['constraints']
        code = 'stream_plan_layout_unavailable'
        if False not in constraints.values():
            frame_context = {'version': descriptor['protocol_version'], 'session_id': context['session_id']}
            layout, refusal = _compiler_stream_data_layout(files, frame_context, wire, ceiling,
                started + image['capture_ms'] / 1000)
            if refusal:
                constraints[refusal] = False
            else:
                constraints['data_wire'] = True
                manifest = {'version': descriptor['protocol_version'], 'session_id': context['session_id'],
                    'owner_context_sha256': context['owner_context_sha256'], 'context_sha256': context_sha,
                    'policy_sha256': _semantic_metadata_sha(descriptor), 'schema_sha256': descriptor['schema_sha256'],
                    'inventory_sha256': metadata['inventory_sha256'], 'compiler_version': metadata['compiler_version'],
                    'compiler_source_sha256': metadata['installed_module_sha256'], 'mapping_root': descriptor['mapping_root'],
                    'files': files, 'layout': layout}
                manifest_bytes = len(_semantic_metadata_json(manifest).encode('utf-8'))
                manifest_sha = _semantic_metadata_sha(manifest)
                first = _compiler_stream_jsonl_size('open', frame_context, manifest_sha, 0, manifest=manifest)
                last = _compiler_stream_jsonl_size('commit', frame_context, manifest_sha, len(layout) + 1,
                    files=count, raw_bytes=raw_total)
                frames = [{'seq': 0, 'kind': 'open', 'jsonl_bytes': first, 'records': 0}]
                frames += [{'seq': row['seq'], 'kind': 'data', 'jsonl_bytes': row['jsonl_bytes'],
                    'records': len(row['records'])} for row in layout]
                frames.append({'seq': len(layout) + 1, 'kind': 'commit', 'jsonl_bytes': last, 'records': 0})
                fragments = [0] * count
                for frame in layout:
                    for row in frame['records']:
                        fragments[row['file_index']] += 1
                constraints.update(open_wire=first <= wire, commit_wire=last <= wire,
                    manifest_metadata=manifest_bytes <= ceiling)
                result.update(manifest=manifest, manifest_sha256=manifest_sha, frames=frames,
                    wire_bytes_total=sum(row['jsonl_bytes'] for row in frames),
                    largest_frame_jsonl_bytes=max(row['jsonl_bytes'] for row in frames),
                    manifest_json_bytes=manifest_bytes, record_count=sum(fragments),
                    chunked_files=sum(n > 1 for n in fragments))
        constraints['result_metadata'] = True
        result['status'] = descriptor['budget_status'] if False in constraints.values() else descriptor['layout_status']
        result['budget_reason'] = next((key for key, value in constraints.items() if value is False), None)
        if len(_semantic_metadata_json(result).encode('utf-8')) > ceiling:
            constraints['result_metadata'] = False
            result.update(status=descriptor['budget_status'], budget_reason='result_metadata', manifest=None, manifest_sha256=None, frames=None,
                wire_bytes_total=None, largest_frame_jsonl_bytes=None, manifest_json_bytes=None,
                record_count=None, chunked_files=None)
        code = 'stream_plan_owner_changed'
        current = capsule.library_bytes()
        if (_semantic_metadata_json(capsule.metadata()) != _semantic_metadata_json(metadata)
                or set(current) != set(libraries) or any(current[k] is not value for k, value in libraries.items())):
            raise ValueError
        # Empty rosters have no range loop; enforce the same invocation deadline
        # after final metadata/ownership checks on every available/budget exit.
        if time.monotonic() >= started + image['capture_ms'] / 1000:
            raise TimeoutError('stream_layout_time_budget')
        return result
    except (KeyError, TypeError, ValueError, UnicodeError, TimeoutError) as error:
        # Fixed stage only; no input/decoder text. Resource failure/cancel propagate.
        return {**empty, 'constraints': {key: None for key in empty['constraints']},
            'code': 'stream_plan_time_budget' if isinstance(error, TimeoutError) else code}


class _CompilerStreamEncoderRefused(ValueError):
    """Curated private model refusal; never an arbitrary input/error echo."""


def _compiler_stream_manifest_summary(manifest, expected_manifest_sha256, wire):
    """One metadata geometry/accounting owner for encoder and independent stager."""
    files = manifest['files']; count = len(files); total = sum(row['bytes'] for row in files)
    frame_context = {k: manifest[k] for k in ('version','session_id')}
    offsets, fragments, last_index = [0] * count, [0] * count, -1
    frames = [{'seq':0,'kind':'open','jsonl_bytes':_compiler_stream_jsonl_size('open',frame_context,expected_manifest_sha256,0,manifest=manifest),'records':0}]
    for seq, row in enumerate(manifest['layout'], 1):
        if row['seq'] != seq:
            raise ValueError
        for record in row['records']:
            index, offset, length = (record[k] for k in ('file_index','offset','raw_bytes'))
            if (index >= count or index < last_index or offset != offsets[index] or offset + length > files[index]['bytes']):
                raise ValueError
            offsets[index] += length; fragments[index] += 1; last_index = index
        size = _compiler_stream_jsonl_size('data',frame_context,expected_manifest_sha256,seq,records=row['records'])
        if row['jsonl_bytes'] != size:
            raise ValueError
        frames.append({'seq':seq,'kind':'data','jsonl_bytes':size,'records':len(row['records'])})
    frames.append({'seq':len(frames),'kind':'commit','jsonl_bytes':_compiler_stream_jsonl_size('commit',frame_context,
        expected_manifest_sha256,len(frames),files=count,raw_bytes=total),'records':0})
    derived = {'frames':frames,'library_files':count,'library_raw_bytes':total,'frame_limit_bytes':wire['max_request_bytes'],
        'wire_bytes_total':sum(row['jsonl_bytes'] for row in frames),'largest_frame_jsonl_bytes':max(row['jsonl_bytes'] for row in frames),
        'manifest_json_bytes':len(_semantic_metadata_json(manifest).encode('utf-8')),'record_count':sum(fragments),'chunked_files':sum(n>1 for n in fragments)}
    if offsets != [row['bytes'] for row in files] or derived['largest_frame_jsonl_bytes'] > wire['max_request_bytes']:
        raise ValueError
    return derived


def _compiler_stream_encoder_plan(capsule, held, expected_manifest_sha256):
    """Validate a pinned recipe using the existing format owner, never replan."""
    from tools.core.artifact_validator import _validate
    profiles, schema, plan = (held[k] for k in ('profiles', 'schema', 'plan'))
    descriptor = profiles['private_compiler_stream_layout']
    errors = []
    for value, definition in ((descriptor,'stream_policy'), (held['context'],'stream_context'), (plan,'stream_result')):
        _validate(value, schema['$defs'][definition], schema, [definition], errors)
    if (errors or descriptor['schema_sha256'] != _semantic_metadata_sha(schema)
            or plan['status'] != descriptor['layout_status'] or not _config_digest(expected_manifest_sha256)
            or plan['manifest_sha256'] != expected_manifest_sha256):
        raise ValueError
    if (type(capsule) is not _CompilerLibraryCapsule
            or not isinstance(getattr(capsule, '_metadata_json', None), str)
            or type(getattr(capsule, '_libraries', None)) is not tuple):
        raise ValueError
    metadata, libraries = capsule.metadata(), capsule.library_bytes()
    manifest = plan['manifest']; context = held['context']
    wire = profiles[descriptor['wire_profile']]; image = profiles[descriptor['image_profile']]['limits']
    capture = profiles[descriptor['capture_profile']]; bridge = profiles[wire['bridge_profile']]
    limits = [wire['max_request_bytes'], bridge['max_response_bytes'], bridge['max_request_id_chars'], image['metadata_bytes'],
        image['capture_ms'], image['compiler_files'], image['compiler_bytes'], image['compiler_file_bytes'], image['image_bytes'],
        capture['max_files'], capture['max_file_bytes'], capture['max_total_bytes']]
    if any(type(n) is not int or n < 1 for n in limits):
        raise ValueError
    if (not libraries or _semantic_metadata_json(metadata) != _semantic_metadata_json(held['expected_inventory'])
            or metadata['status'] != 'captured' or metadata['snapshot_bound'] is not False or metadata['loaded_code_attested'] is not False
            or set(metadata['files']) != set(libraries) or len(context['session_id']) > bridge['max_request_id_chars']):
        raise ValueError
    files = []
    for name, raw in sorted(libraries.items()):
        entry = metadata['files'][name]
        if (type(raw) is not bytes or re.fullmatch(r'lib[A-Za-z0-9_.-]*\.d\.ts', name) is None
                or hashlib.sha256(raw).hexdigest() != entry['sha256'] or not _config_digest(entry['host_text_sha256'])):
            raise ValueError
        files.append({'name': name, 'bytes': len(raw), 'raw_sha256': entry['sha256'], 'host_text_sha256': entry['host_text_sha256']})
    total, count = sum(row['bytes'] for row in files), len(files)
    if (len({row['name'].lower() for row in files}) != count or count > min(capture['max_files'], image['compiler_files'])
            or total > min(capture['max_total_bytes'], image['compiler_bytes'], image['image_bytes'])
            or any(row['bytes'] > min(capture['max_file_bytes'], image['compiler_file_bytes']) for row in files)):
        raise ValueError
    expected = {'version': descriptor['protocol_version'], 'session_id': context['session_id'],
        'owner_context_sha256': context['owner_context_sha256'], 'context_sha256': _semantic_metadata_sha({'context':context,'inventory':metadata,'profiles':profiles}),
        'policy_sha256': _semantic_metadata_sha(descriptor), 'schema_sha256': descriptor['schema_sha256'],
        'inventory_sha256': metadata['inventory_sha256'], 'compiler_version': metadata['compiler_version'],
        'compiler_source_sha256': metadata['installed_module_sha256'], 'mapping_root': descriptor['mapping_root'], 'files': files}
    if (any(manifest[k] != v for k,v in expected.items()) or _semantic_metadata_sha(manifest) != expected_manifest_sha256
            or plan['context_sha256'] != expected['context_sha256'] or plan['inventory_sha256'] != expected['inventory_sha256']):
        raise ValueError
    derived = _compiler_stream_manifest_summary(manifest, expected_manifest_sha256, wire)
    if any(plan[k] != v for k,v in derived.items()) or derived['manifest_json_bytes'] > image['metadata_bytes']:
        raise ValueError
    return _semantic_metadata_json(metadata), libraries


class _CompilerStreamEncoder:
    """Private one-frame-credit memory model; never opens IO or closes its capsule."""
    __slots__ = ('_capsule','_metadata','_libraries','_held','_deadline','_refs','_state','_code','_seq',
        '_count','_bytes','_pending','_pending_sha','_transcript')

    def __init__(self, capsule, held, metadata, libraries, deadline):
        self._capsule, self._held, self._metadata, self._libraries, self._deadline = capsule, held, metadata, libraries, deadline
        plan = held['plan']
        self._refs = {k:plan[k] for k in ('context_sha256','inventory_sha256','manifest_sha256')}
        self._state, self._code, self._seq, self._count, self._bytes = 'READY', '', 0, 0, 0
        self._pending = self._pending_sha = None
        self._transcript = hashlib.sha256()

    def __repr__(self):
        return '<private compiler encoder ' + self._state + '>'

    def __reduce_ex__(self, protocol):
        raise TypeError('compiler_stream_encoder_not_serializable')

    def _clear(self):
        self._capsule = self._held = self._metadata = self._libraries = self._pending = self._pending_sha = self._transcript = None

    def _reject(self, code):
        if self._state != 'DISPOSED':
            self._state = 'ABORTED'
        self._code = code; self._clear()
        raise _CompilerStreamEncoderRefused(code) from None

    def _check_live(self):
        import time
        if self._state in ('ABORTED','DISPOSED','COMPLETE'):
            self._reject('stream_encoder_order')
        if time.monotonic() >= self._deadline:
            self._reject('stream_encoder_deadline')
        capsule = self._capsule
        if not isinstance(getattr(capsule, '_metadata_json', None), str):
            self._reject('stream_encoder_owner_unavailable')
        current = capsule.library_bytes()
        if (_semantic_metadata_json(capsule.metadata()) != self._metadata or set(current) != set(self._libraries)
                or any(current[k] is not value for k,value in self._libraries.items())):
            self._reject('stream_encoder_owner_changed')

    def _frame(self):
        import base64
        plan = self._held['plan']; manifest = plan['manifest']; spec = plan['frames'][self._seq]
        context = {k:manifest[k] for k in ('version','session_id')}; rows = []
        if spec['kind'] == 'data':
            for slot in manifest['layout'][self._seq-1]['records']:
                raw = self._libraries[manifest['files'][slot['file_index']]['name']][slot['offset']:slot['offset']+slot['raw_bytes']]
                rows.append({**slot,'chunk_sha256':hashlib.sha256(raw).hexdigest(),'utf8Base64':base64.b64encode(raw).decode('ascii')})
        body = _compiler_stream_frame_fields(spec['kind'],context,plan['manifest_sha256'],self._seq,records=rows,manifest=manifest,
            files=plan['library_files'],raw_bytes=plan['library_raw_bytes'],transcript_sha256=self._transcript.hexdigest())
        frame = (_semantic_metadata_json(body) + '\n').encode('utf-8')
        if len(frame) != spec['jsonl_bytes'] or len(frame) > plan['frame_limit_bytes']:
            self._reject('stream_encoder_frame_mismatch')
        return frame

    def _operate(self, action, ack=None):
        from tools.core.artifact_validator import _validate
        settled = False
        try:
            self._check_live()
            result = None
            if action == 'next':
                if self._state != 'READY': self._reject('stream_encoder_order')
                frame = self._frame(); self._check_live()
                self._pending, self._pending_sha = frame, hashlib.sha256(frame).hexdigest()
                kind = self._held['plan']['frames'][self._seq]['kind']
                if kind != 'commit': self._transcript.update(frame)
                self._count += 1; self._bytes += len(frame)
                self._state = 'COMMIT_PENDING_EOF' if kind == 'commit' else 'AWAITING_ACK'
                result = frame
            elif action == 'enter':
                result = self
            elif action == 'close':
                if self._state != 'COMMIT_PENDING_EOF': self._reject('stream_encoder_order')
                self._state = 'AWAITING_FINAL_ACK'
            else:
                if self._state not in ('AWAITING_ACK','AWAITING_FINAL_ACK'): self._reject('stream_encoder_order')
                plan = self._held['plan']; profiles = self._held['profiles']; schema = self._held['schema']
                layout = profiles['private_compiler_stream_layout']; wire = profiles[layout['wire_profile']]
                text = _semantic_metadata_json(ack)
                if len(text.encode('utf-8')) > min(profiles[wire['bridge_profile']]['max_response_bytes'],profiles[layout['image_profile']]['limits']['metadata_bytes']):
                    self._reject('stream_encoder_ack_invalid')
                held_ack = json.loads(text); errors = []
                _validate(held_ack,schema['$defs']['encoder_ack'],schema,['encoder_ack'],errors)
                expected = {'version':plan['manifest']['version'],'session_id':plan['manifest']['session_id'],
                    'manifest_sha256':plan['manifest_sha256'],'seq':self._seq,'frame_sha256':self._pending_sha,
                    'state':'COMPLETE' if self._state=='AWAITING_FINAL_ACK' else 'STAGED'}
                if errors or held_ack != expected: self._reject('stream_encoder_ack_invalid')
                self._pending = self._pending_sha = None; self._seq += 1
                self._state = 'FINISHING' if expected['state']=='COMPLETE' else 'READY'
            self._check_live()
            if self._state == 'FINISHING':
                self._state = 'COMPLETE'; self._clear()
            settled = True
            return result
        except _CompilerStreamEncoderRefused:
            raise
        except (MemoryError, KeyboardInterrupt):
            self._state = 'ABORTED'; self._code = 'stream_encoder_interrupted'; self._clear(); raise
        except (KeyError, TypeError, ValueError, UnicodeError, OverflowError):
            self._reject('stream_encoder_internal')
        finally:
            # Cleanup also covers unexpected exceptions; preserve their identity.
            if not settled:
                if self._state != 'DISPOSED': self._state = 'ABORTED'
                if not self._code: self._code = 'stream_encoder_internal'
                self._clear()

    def next_frame(self):
        return self._operate('next')

    def accept_ack(self, metadata):
        return self._operate('ack', metadata)

    def mark_input_closed(self):
        """Model marker only: no pipe operation and no claim EOF was observed."""
        return self._operate('close')

    def snapshot(self):
        return {'version':'v1-private-compiler-stream-encoder','status':'COMPILER_STREAM_ENCODER_MODEL_ONLY_NOT_SENT',
            'state':self._state,'code':self._code,**self._refs,'next_seq':self._seq,'frames_encoded':self._count,'encoded_bytes':self._bytes,
            'outstanding_frame_sha256':self._pending_sha,'raw_frame_encoded':self._count>0,
            'whole_image_within_limit':None,'peak_rss_bytes':None,
            **{k:False for k in ('raw_frame_emitted','raw_content_verified','base_content_verified','send_verified','source_freshness_verified',
                'snapshot_bound','loaded_code_attested','native_execution_attested','production_enabled','semantic_lineage','cache_eligible')}}

    def dispose(self):
        self._state = 'DISPOSED'; self._clear()

    def __enter__(self):
        return self._operate('enter')

    def __exit__(self, exc_type, exc, traceback):
        self.dispose()


def _begin_owned_compiler_stream_encoder(capsule, *, expected_inventory, context, profiles, schema,
                                         plan, expected_manifest_sha256, deadline):
    """Explicit disabled synthetic memory codec; no default/IO/native consumer."""
    import math
    import time
    from tools.core.artifact_validator import _validate
    code = 'stream_encoder_policy_invalid'
    try:
        descriptor = profiles['private_compiler_stream_encoder']; layout = profiles[descriptor['layout_profile']]
        image = profiles[layout['image_profile']]['limits']; ceiling = image['metadata_bytes']
        if type(ceiling) is not int or ceiling < 1: raise ValueError
        text = _semantic_metadata_json({'expected_inventory':expected_inventory,'context':context,'profiles':profiles,'schema':schema,'plan':plan})
        if len(text.encode('utf-8')) > ceiling: raise ValueError
        held = json.loads(text); descriptor = held['profiles']['private_compiler_stream_encoder']; errors = []
        _validate(descriptor,held['schema']['$defs']['encoder_policy'],held['schema'],['encoder_policy'],errors)
        if errors or descriptor['schema_sha256'] != _semantic_metadata_sha(held['schema']): raise ValueError
        layout = held['profiles'][descriptor['layout_profile']]
        image = held['profiles'][layout['image_profile']]['limits']
        code = 'stream_encoder_deadline'; now = time.monotonic()
        if (type(deadline) not in (int,float) or not math.isfinite(deadline) or deadline <= now
                or type(image['capture_ms']) is not int or image['capture_ms'] < 1 or deadline > now + image['capture_ms']/1000): raise ValueError
        code = 'stream_encoder_plan_invalid'
        metadata, libraries = _compiler_stream_encoder_plan(capsule, held, expected_manifest_sha256)
        encoder = _CompilerStreamEncoder(capsule,held,metadata,libraries,deadline)
        encoder._check_live()
        return encoder
    except _CompilerStreamEncoderRefused:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, OverflowError):
        raise _CompilerStreamEncoderRefused(code) from None


class _CompilerStreamStagerRefused(ValueError):
    """Curated independent raw-staging model refusal."""


def _compiler_stream_stager_manifest(manifest, held):
    """Pin and reserve a complete bounded roster before allocating raw buffers."""
    from tools.core.artifact_validator import _validate
    profiles, schema, expected = (held[k] for k in ('profiles','schema','expected'))
    descriptor = profiles['private_compiler_stream_layout']; errors = []
    _validate(manifest,schema['$defs']['stream_manifest'],schema,['stream_manifest'],errors)
    if (errors or _semantic_metadata_sha(manifest) != expected['manifest_sha256']
            or any(manifest[k] != value for k,value in expected.items() if k != 'manifest_sha256')
            or manifest['version'] != descriptor['protocol_version'] or manifest['mapping_root'] != descriptor['mapping_root']):
        raise ValueError
    files=manifest['files']; capture=profiles[descriptor['capture_profile']]; image=profiles[descriptor['image_profile']]['limits']
    total=sum(row['bytes'] for row in files)
    if (len(files) > min(capture['max_files'],image['compiler_files'])
            or total > min(capture['max_total_bytes'],image['compiler_bytes'],image['image_bytes'])
            or any(row['bytes'] > min(capture['max_file_bytes'],image['compiler_file_bytes']) for row in files)):
        raise _CompilerStreamStagerRefused('stream_stager_budget')
    if ([row['name'] for row in files] != sorted(row['name'] for row in files)
            or len({row['name'].lower() for row in files}) != len(files)
            or any(row['bytes']==0 and row['raw_sha256']!=hashlib.sha256(b'').hexdigest() for row in files)):
        raise ValueError
    summary=_compiler_stream_manifest_summary(manifest,expected['manifest_sha256'],profiles[descriptor['wire_profile']])
    if summary['manifest_json_bytes'] > image['metadata_bytes']:
        raise _CompilerStreamStagerRefused('stream_stager_budget')
    return summary


class _CompilerStreamStager:
    """Independent Python byte-staging model; no capsule, filesystem or compiler."""
    __slots__=('_held','_deadline','_refs','_state','_code','_incoming','_manifest','_summary','_seq','_credit',
        '_transcript','_commit_sha','_active','_offset','_file_index','_completed','_frames','_wire_bytes','_raw_bytes','_verified')

    def __init__(self,held,deadline):
        self._held,self._deadline=held,deadline
        self._refs={k:held['expected'][k] for k in ('context_sha256','inventory_sha256','manifest_sha256')}
        self._state,self._code,self._seq='NEW','',0
        self._incoming=bytearray();self._manifest=self._summary=self._credit=self._commit_sha=self._active=None
        self._transcript=hashlib.sha256();self._offset=self._file_index=self._frames=self._wire_bytes=self._raw_bytes=0
        self._completed=[];self._verified=False

    def __repr__(self):return '<private compiler stager '+self._state+'>'

    def __reduce_ex__(self,protocol):raise TypeError('compiler_stream_stager_not_serializable')

    def _clear(self):
        self._held=self._incoming=self._manifest=self._summary=self._credit=self._transcript=self._commit_sha=self._active=self._completed=None

    def _reject(self,code):
        if self._state!='DISPOSED':self._state='ABORTED'
        self._code=code;self._verified=False;self._clear()
        raise _CompilerStreamStagerRefused(code) from None

    def _check(self):
        import time
        if self._state in ('ABORTED','DISPOSED'):self._reject('stream_stager_order')
        if time.monotonic()>=self._deadline:self._reject('stream_stager_deadline')

    def _ack(self,seq,frame_sha,state):
        return {'version':self._held['profiles']['private_compiler_stream_layout']['protocol_version'],
            'session_id':self._held['expected']['session_id'],'manifest_sha256':self._refs['manifest_sha256'],
            'seq':seq,'frame_sha256':frame_sha,'state':state}

    def _skip_empty(self):
        files=self._manifest['files']
        while self._file_index<len(files) and files[self._file_index]['bytes']==0:
            self._completed.append(bytearray());self._file_index+=1

    def _stage_record(self,row,slot):
        import base64
        from tools.core.artifact_validator import _validate
        self._check();errors=[];schema=self._held['schema']
        _validate(row,schema['$defs']['stager_record'],schema,['stager_record'],errors)
        if errors or {k:value for k,value in row.items() if k not in ('chunk_sha256','utf8Base64')}!=slot:
            self._reject('stream_stager_record_invalid')
        raw=base64.b64decode(row['utf8Base64'].encode('ascii'),validate=True)
        if (len(raw)!=slot['raw_bytes'] or base64.b64encode(raw).decode('ascii')!=row['utf8Base64']
                or hashlib.sha256(raw).hexdigest()!=row['chunk_sha256']):self._reject('stream_stager_raw_identity')
        if slot['file_index']!=self._file_index or slot['offset']!=self._offset:self._reject('stream_stager_record_invalid')
        file=self._manifest['files'][self._file_index]
        if self._active is None:self._active=bytearray(file['bytes'])
        self._active[self._offset:self._offset+len(raw)]=raw;self._offset+=len(raw);self._raw_bytes+=len(raw)
        if self._offset==file['bytes']:
            if hashlib.sha256(self._active).hexdigest()!=file['raw_sha256']:self._reject('stream_stager_raw_identity')
            self._completed.append(self._active);self._active=None;self._offset=0;self._file_index+=1;self._skip_empty()

    def _accept_frame(self,frame):
        from tools.core.json_syntax import loads_json_strict
        value=loads_json_strict(frame[:-1].decode('utf-8'))
        if (_semantic_metadata_json(value).encode('utf-8')+b'\n'!=frame or type(value) is not dict
                or type(value.get('seq')) is not int or value['seq']!=self._seq):self._reject('stream_stager_frame_invalid')
        expected=self._held['expected'];layout=self._held['profiles']['private_compiler_stream_layout']
        context={'version':layout['protocol_version'],'session_id':expected['session_id']}
        kind='open' if self._state=='NEW' else self._summary['frames'][self._seq]['kind']
        if value.get('kind')!=kind:self._reject('stream_stager_order')
        manifest=None;rows=();files=raw_bytes=0;transcript=None
        if kind=='open':
            manifest=value['manifest'];summary=_compiler_stream_stager_manifest(manifest,self._held)
        else:
            summary=self._summary
            if len(frame)!=summary['frames'][self._seq]['jsonl_bytes']:self._reject('stream_stager_frame_invalid')
            if kind=='data':
                rows=value['records'];slots=self._manifest['layout'][self._seq-1]['records']
                if type(rows) is not list or len(rows)!=len(slots):self._reject('stream_stager_record_invalid')
            else:
                if type(value.get('files_complete')) is not int or type(value.get('raw_bytes_total')) is not int:self._reject('stream_stager_frame_invalid')
                files,raw_bytes=summary['library_files'],summary['library_raw_bytes'];transcript=self._transcript.hexdigest()
                if self._file_index!=files or self._raw_bytes!=raw_bytes or self._active is not None:self._reject('stream_stager_raw_identity')
        body=_compiler_stream_frame_fields(kind,context,expected['manifest_sha256'],self._seq,
            manifest=manifest,records=rows,files=files,raw_bytes=raw_bytes,transcript_sha256=transcript)
        if value!=body or len(frame)!=summary['frames'][self._seq]['jsonl_bytes']:self._reject('stream_stager_frame_invalid')
        if kind=='open':
            self._manifest,self._summary=manifest,summary;self._skip_empty();self._state='OPEN'
        elif kind=='data':
            for row,slot in zip(rows,slots):self._stage_record(row,slot)
            self._state='RECEIVING'
        else:self._state='SEALED';self._commit_sha=hashlib.sha256(frame).hexdigest()
        self._frames+=1;self._wire_bytes+=len(frame);self._seq+=1
        if kind=='commit':return None
        self._transcript.update(frame)
        self._credit=self._ack(self._seq-1,hashlib.sha256(frame).hexdigest(),'STAGED')
        return dict(self._credit)

    def _feed(self,data):
        if self._state=='COMPLETE':self._reject('stream_stager_order')
        if type(data) is not bytes:self._reject('stream_stager_frame_invalid')
        if not data:return None
        if self._state=='SEALED':self._reject('stream_stager_trailing')
        if self._credit is not None:self._reject('stream_stager_credit')
        layout=self._held['profiles']['private_compiler_stream_layout'];limit=self._held['profiles'][layout['wire_profile']]['max_request_bytes']
        if len(self._incoming)+len(data)>limit:self._reject('stream_stager_budget')
        newline=data.find(b'\n')
        if newline>=0 and newline!=len(data)-1:self._reject('stream_stager_trailing')
        self._incoming.extend(data)
        if newline<0:return None
        frame=bytes(self._incoming);self._incoming.clear()
        return self._accept_frame(frame)

    def _release(self,ack):
        from tools.core.artifact_validator import _validate
        if self._credit is None:self._reject('stream_stager_credit')
        text=_semantic_metadata_json(ack);layout=self._held['profiles']['private_compiler_stream_layout']
        wire=self._held['profiles'][layout['wire_profile']];profiles=self._held['profiles']
        if len(text.encode('utf-8'))>min(profiles[layout['image_profile']]['limits']['metadata_bytes'],profiles[wire['bridge_profile']]['max_response_bytes']):self._reject('stream_stager_credit')
        copied=json.loads(text);errors=[];schema=self._held['schema']
        _validate(copied,schema['$defs']['encoder_ack'],schema,['model_ack_delivery'],errors)
        if errors or copied!=self._credit:self._reject('stream_stager_credit')
        self._credit=None

    def _operate(self,action,payload=None):
        settled=borrowing=False
        try:
            self._check();result=None
            if action=='feed':result=self._feed(payload)
            elif action=='release':self._release(payload)
            elif action=='eof':
                if self._state!='SEALED' or self._incoming or self._credit is not None:self._reject('stream_stager_incomplete')
                if self._wire_bytes!=self._summary['wire_bytes_total']:self._reject('stream_stager_raw_identity')
                self._state='COMPLETE';self._verified=True;result=self._ack(self._seq-1,self._commit_sha,'COMPLETE')
            elif action=='consume':
                if self._state!='COMPLETE' or not callable(payload):self._reject('stream_stager_order')
                self._state='BORROWING';borrowing=True
                result=payload(tuple((row['name'],memoryview(raw).toreadonly()) for row,raw in zip(self._manifest['files'],self._completed)))
            else:self._reject('stream_stager_order')
            self._check()
            if borrowing:self._state='DISPOSED';self._clear()
            settled=True;return result
        except _CompilerStreamStagerRefused as error:
            if not self._code and not borrowing:
                allowed=self._held['profiles']['private_compiler_stream_stager']['refusal_codes']
                self._code=str(error) if str(error) in allowed else 'stream_stager_internal'
            raise
        except (MemoryError,KeyboardInterrupt):
            self._code='stream_stager_interrupted';raise
        except (KeyError,TypeError,ValueError,UnicodeError,OverflowError,RecursionError):
            if borrowing:raise
            self._reject('stream_stager_frame_invalid')
        finally:
            if not settled:
                if self._state!='DISPOSED':self._state='ABORTED'
                if not self._code:self._code='stream_stager_internal'
                self._verified=False;self._clear()

    def feed(self,data):return self._operate('feed',data)

    def release_credit(self,ack):
        """Explicit model ACK-delivery marker only; no IO attestation."""
        return self._operate('release',ack)

    def end_input(self):
        """Explicit model clean-input-end marker; never an OS EOF receipt."""
        return self._operate('eof')

    def consume(self,callback):return self._operate('consume',callback)

    def snapshot(self):
        return {'version':'v1-private-compiler-stream-stager','status':'COMPILER_STREAM_STAGER_MODEL_ONLY_NOT_IMAGE_ADMITTED',
            'state':self._state,'code':self._code,**self._refs,'next_seq':self._seq,'frames_accepted':self._frames,
            'wire_bytes_accepted':self._wire_bytes,'raw_bytes_staged':self._raw_bytes,'files_completed':self._file_index,
            'pending_bytes':len(self._incoming) if self._incoming is not None else 0,
            'credit_frame_sha256':self._credit['frame_sha256'] if self._credit is not None else None,
            'raw_bytes_verified_in_model':self._verified,'whole_image_within_limit':None,'peak_rss_bytes':None,
            **{k:False for k in ('actual_transfer','raw_content_verified','host_text_verified','source_freshness_verified','snapshot_bound',
                'loaded_code_attested','native_execution_attested','production_enabled','semantic_lineage','cache_eligible')}}

    def dispose(self):self._state='DISPOSED';self._clear()

    def __enter__(self):self._check();return self

    def __exit__(self,exc_type,exc,traceback):self.dispose()


def _begin_owned_compiler_stream_stager(expected,*,profiles,schema,deadline):
    """Independent disabled Python model; accepts references, never sender bytes."""
    import math
    import time
    from tools.core.artifact_validator import _validate
    code='stream_stager_policy_invalid'
    try:
        descriptor=profiles['private_compiler_stream_stager'];layout=profiles[descriptor['layout_profile']]
        ceiling=profiles[layout['image_profile']]['limits']['metadata_bytes']
        if type(ceiling) is not int or ceiling<1:raise ValueError
        text=_semantic_metadata_json({'expected':expected,'profiles':profiles,'schema':schema})
        if len(text.encode('utf-8'))>ceiling:raise ValueError
        held=json.loads(text);profiles,schema,expected=(held[k] for k in ('profiles','schema','expected'))
        descriptor=profiles['private_compiler_stream_stager'];layout=profiles[descriptor['layout_profile']]
        image=profiles[layout['image_profile']]['limits'];wire=profiles[layout['wire_profile']];capture=profiles[layout['capture_profile']];bridge=profiles[wire['bridge_profile']]
        errors=[]
        for value,definition in ((descriptor,'stager_policy'),(layout,'stream_policy'),(expected,'stager_expected')):
            _validate(value,schema['$defs'][definition],schema,[definition],errors)
        limits=[image['metadata_bytes'],image['capture_ms'],image['compiler_files'],image['compiler_bytes'],image['compiler_file_bytes'],image['image_bytes'],
            capture['max_files'],capture['max_file_bytes'],capture['max_total_bytes'],wire['max_request_bytes'],bridge['max_response_bytes'],bridge['max_request_id_chars']]
        digest=_semantic_metadata_sha(schema)
        if (errors or any(type(n) is not int or n<1 for n in limits) or len(text.encode('utf-8'))>image['metadata_bytes']
                or descriptor['schema_sha256']!=digest or layout['schema_sha256']!=digest):raise ValueError
        code='stream_stager_expected_invalid'
        if (len(expected['session_id'])>bridge['max_request_id_chars'] or expected['schema_sha256']!=digest
                or expected['policy_sha256']!=_semantic_metadata_sha(layout)):raise ValueError
        code='stream_stager_deadline';now=time.monotonic()
        if type(deadline) not in (int,float) or not math.isfinite(deadline) or not now<deadline<=now+image['capture_ms']/1000:raise ValueError
        stager=_CompilerStreamStager(held,deadline);stager._check();return stager
    except _CompilerStreamStagerRefused:raise
    except (KeyError,TypeError,ValueError,UnicodeError,OverflowError,RecursionError):
        raise _CompilerStreamStagerRefused(code) from None


def _plan_owned_compiler_transport_from_wire_base(capsule, *, expected_inventory,
                                                receiver, receipt, expected_wire,
                                                profiles, schemas, include_package=False):
    """Bind a completed private construction receipt to the existing size view.

    This adapter performs no new capture, encoding, size formula or execution.
    Separate owner/inventory references are caller-owned, not hostile-caller
    authentication. The planner's unknown/false admission fields stay unchanged.
    """
    from tools.core.artifact_validator import _validate
    from tools.core.owned_text_bridge import owned_wire_base_plan_context, config_input_policy_identity
    output = {'version': 'v1-private-wire-base-compiler-plan', 'status': 'WIRE_BASE_COMPILER_PLAN_UNAVAILABLE',
        'code': '', 'receipt_sha256': None, 'context_sha256': None, 'plan': None,
        'base_content_verified': False, 'send_verified': False, 'native_execution_attested': False,
        'production_enabled': False, 'semantic_lineage': False, 'cache_eligible': False}
    code = 'wire_plan_policy_unavailable'
    try:
        planner_policy = profiles['private_compiler_transport_plan']
        wire_policy = profiles['private_owned_wire_base']
        ceiling = profiles[planner_policy['image_profile']]['limits']['metadata_bytes']
        if type(ceiling) is not int or ceiling < 1:
            raise ValueError
        text = _semantic_metadata_json({'profiles': profiles, 'schemas': schemas, 'receipt': receipt,
            'expected_wire': expected_wire, 'inventory': expected_inventory})
        code = 'wire_plan_metadata_over_budget'
        if len(text.encode('utf-8')) > ceiling:
            raise ValueError
        held = json.loads(text)
        profiles, schemas, receipt, expected_wire, expected_inventory = (held[k] for k in
            ('profiles', 'schemas', 'receipt', 'expected_wire', 'inventory'))
        code = 'wire_plan_policy_unavailable'
        if set(schemas) != {'plan', 'wire'}:
            raise ValueError
        planner_policy, wire_policy = profiles['private_compiler_transport_plan'], profiles['private_owned_wire_base']
        errors = []
        _validate(planner_policy, schemas['plan'], schemas['plan'], ['planner_policy'], errors)
        _validate(wire_policy, schemas['wire'], schemas['wire'], ['wire_policy'], errors)
        _validate(expected_wire, schemas['plan']['$defs']['wire_adapter_expected'], schemas['plan'], ['wire_expected'], errors)
        if (errors or planner_policy['schema_sha256'] != _semantic_metadata_sha(schemas['plan'])
                or wire_policy['schema_sha256'] != _semantic_metadata_sha(schemas['wire'])):
            raise ValueError
        code = 'wire_plan_owner_unavailable'
        observed = owned_wire_base_plan_context(receiver)
        if observed is None:
            raise ValueError
        # A copied call-local receipt must match the typed owner, not only its
        # own supplied digest. Owner metadata is read atomically in the leaf.
        code = 'wire_plan_receipt_refused'
        _validate(receipt, schemas['wire']['$defs']['receipt'], schemas['wire'], ['wire_receipt'], errors)
        if (errors or receipt['status'] != 'OWNED_WIRE_BASE_DERIVED_NOT_ADMITTED'
                or _semantic_metadata_json(receipt) != _semantic_metadata_json(observed['receipt'])):
            raise ValueError
        code = 'wire_plan_identity_mismatch'
        receipt_sha = _semantic_metadata_sha(receipt)
        if (receipt_sha != expected_wire['receipt_sha256']
                or any(receipt[k] != expected_wire[k] for k in ('binding_sha256', 'parent_ref_sha256'))
                or any(observed[k] != expected_wire[k] for k in ('source_request_id', 'config_invocation_id'))):
            raise ValueError
        source_policy = profiles[wire_policy['source_profile']]
        config_policy = profiles[wire_policy['config_profile']]
        extends_policy = profiles[wire_policy['extends_profile']]
        if (observed['source_policy_sha256'] != _semantic_metadata_sha(source_policy)
                or observed['config_policy_sha256'] != _semantic_metadata_sha(extends_policy)
                or observed['auxiliary_policy_sha256'] != _semantic_metadata_sha(profiles['atlas_auxiliary_inputs'])
                or observed['input_policy_sha256'] != config_input_policy_identity(config_policy,
                    profiles[config_policy['image_profile']], profiles[config_policy['bridge_profile']],
                    profiles['atlas_auxiliary_inputs'])):
            raise ValueError
        code = 'wire_plan_compiler_mismatch'
        if (observed['compiler_version'] != expected_inventory['compiler_version']
                or observed['compiler_source_sha256'] != expected_inventory['installed_module_sha256']):
            raise ValueError
        code = 'wire_plan_delegate_unavailable'
        plan = _plan_owned_compiler_library_transport(capsule, expected_inventory=expected_inventory,
            base_frame=receipt['base'], profiles=profiles, schema=schemas['plan'], include_package=include_package)
        text = _semantic_metadata_json(plan)
        if len(text.encode('utf-8')) > ceiling:
            raise ValueError
        plan = json.loads(text)
        code = 'wire_plan_result_refused'
        _validate(plan, schemas['plan']['$defs']['result'], schemas['plan'], ['compiler_plan'], errors)
        if errors:
            raise ValueError
        code = 'wire_plan_delegate_unavailable'
        if plan['status'] == 'COMPILER_TRANSPORT_PLAN_UNAVAILABLE':
            raise ValueError
        code = 'wire_plan_owner_changed'
        if _semantic_metadata_json(owned_wire_base_plan_context(receiver)) != _semantic_metadata_json(observed):
            raise ValueError
        output.update(status='WIRE_BASE_COMPILER_PLAN_RETURNED_NOT_ADMITTED', code='', receipt_sha256=receipt_sha,
            context_sha256=_semantic_metadata_sha({'wire': expected_wire, 'inventory': expected_inventory,
                'profiles': profiles, 'include_package': include_package}), plan=plan)
    except (KeyError, TypeError, ValueError, UnicodeError):
        output['code'] = code
    return output


def capture_compiler_library_inputs(code_maps_dir: Path, run_id: str) -> dict[str, Any]:
    """Capture producer-owned installation inputs, never collector-selected files.

    This is a pre-invocation content image, not an atomic snapshot or an
    attestation of the JavaScript/Node code subsequently executed.
    """
    return _capture_compiler_library_inputs(code_maps_dir, run_id, retain_bytes=False)[0]


def _capture_owned_compiler_library_inputs(code_maps_dir: Path, run_id: str):
    """Opt in to declaration/package bytes from the same canonical read stream.

    The caller owns disposal and any references it keeps after access. No
    executable module, manifest/lock bytes, path remapping or transport is
    retained. Failure returns the unchanged unavailable metadata and no capsule.
    Production callers stay on the metadata-only entry point.
    """
    return _capture_compiler_library_inputs(code_maps_dir, run_id, retain_bytes=True)


def _capture_compiler_library_inputs_with_context(code_maps_dir: Path, run_id: str):
    """Private same-read policy/origin handoff; no raw bytes or release imports.

    The original inventory stays unchanged. This context is supplied metadata,
    not an authentication token or native reuse authority.
    """
    context = {}
    inventory, _ = _capture_compiler_library_inputs(
        code_maps_dir, run_id, retain_bytes=False, capture_context=context)
    return inventory, context


def _capture_compiler_library_inputs(code_maps_dir: Path, run_id: str, *, retain_bytes: bool,
                                     capture_context: dict | None = None):
    result: dict[str, Any] = {
        "version": "v1", "authority": "producer_preinvocation_installation_capture",
        "collector_run_id": run_id, "status": "unavailable", "files": {},
        "scope": "positive_compiler_library_reads_only",
        "loaded_code_attested": False, "snapshot_bound": False,
    }
    retained = {} if retain_bytes else None
    capsule = None
    if capture_context is not None:
        if type(capture_context) is not dict or retain_bytes:
            raise TypeError('compiler_capture_context_requires_metadata_only_sink')
        capture_context.clear()
        capture_context.update(kind='compiler_library_capture_context_v1', status='UNAVAILABLE',
                               expected_origin=None, capture_policy=None,
                               origin_authenticated=False, native_reuse_enabled=False)
    try:
        base = Path(code_maps_dir).resolve(strict=True)
        contract = load_json_object_strict(base / "config/analysis_snapshot_lineage_contract.json")
        policy = contract["artifacts"]["ts_diagnostics"]["compiler_library_inputs"]
        if not isinstance(policy, dict):
            raise ValueError("invalid_compiler_library_budget")
        for key in ("max_directory_entries", "max_files", "max_file_bytes", "max_module_bytes", "max_total_bytes"):
            if type(policy.get(key)) is not int or policy[key] < 1:
                raise ValueError("invalid_compiler_library_budget")
        if capture_context is not None:
            # Freeze the already-read JSON view used by this opt-in capture.
            policy = json.loads(json.dumps(policy, ensure_ascii=True))
        provenance = load_json_object_strict(base / "config/third_party_distribution_contract.json")
        owners = [row for row in provenance["vendored_dependencies"]
                  if isinstance(row, dict) and row.get("id") == "typescript"]
        if len(owners) != 1 or not run_id:
            raise ValueError("compiler_installation_owner_unavailable")
        owner = owners[0]

        def owned_path(relative: str) -> Path:
            if (not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative
                    or PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts):
                raise ValueError("unsafe_compiler_installation_path")
            return base / relative

        total_bytes = 0

        def read_bytes(candidate: Path, limit: int) -> bytes:
            nonlocal total_bytes
            # Installed package roots may be package-manager links. Once that
            # root is fixed, aliased entries cannot silently change its scope.
            if candidate.resolve(strict=True) != candidate.absolute():
                raise ValueError("aliased_compiler_input")
            if not candidate.is_file():
                raise ValueError("non_file_compiler_input")
            with candidate.open("rb") as stream:
                raw = stream.read(min(limit, policy["max_total_bytes"] - total_bytes) + 1)
            total_bytes += len(raw)
            if len(raw) > limit or total_bytes > policy["max_total_bytes"]:
                raise ValueError("compiler_input_budget_exceeded")
            return raw

        manifest_raw = read_bytes(owned_path(owner["manifest_path"]), policy["max_file_bytes"])
        lock_raw = read_bytes(owned_path(owner["lockfile_path"]), policy["max_file_bytes"])
        if hashlib.sha256(lock_raw).hexdigest() != owner["lockfile_sha256"]:
            raise ValueError("compiler_installation_lock_mismatch")
        marker = owned_path(owner["runtime_package_path"])
        package_root = marker.parent.resolve(strict=True)
        package_raw = read_bytes(package_root / marker.name, policy["max_file_bytes"])
        package = json.loads(package_raw.decode("utf-8-sig"))
        manifest = json.loads(manifest_raw.decode("utf-8-sig"))
        version = owner["locked_version"]
        if (not isinstance(package, dict) or not isinstance(manifest, dict)
                or not isinstance(manifest.get("dependencies"), dict)
                or package.get("name") != "typescript" or package.get("version") != version
                or manifest.get("dependencies", {}).get("typescript") != version):
            raise ValueError("compiler_installation_version_mismatch")
        main = package.get("main")
        if (not isinstance(main, str) or not main or "\\" in main or ":" in main
                or PurePosixPath(main).is_absolute() or ".." in PurePosixPath(main).parts):
            raise ValueError("unsafe_compiler_entry")
        module = package_root / main
        module_raw = read_bytes(module, policy["max_module_bytes"])
        library_root = module.parent
        names = []
        with os.scandir(library_root) as entries:
            for count, entry in enumerate(entries, 1):
                if count > policy["max_directory_entries"]:
                    raise ValueError("compiler_directory_budget_exceeded")
                if entry.name.startswith("lib") and entry.name.endswith(".d.ts"):
                    names.append(entry.name)
                    if len(names) > policy["max_files"]:
                        raise ValueError("compiler_library_budget_exceeded")
        if not names:
            raise ValueError("compiler_libraries_unavailable")
        files = {}
        for name in sorted(names):
            raw = read_bytes(library_root / name, policy["max_file_bytes"])
            text = raw.decode("utf-8-sig")  # Preserve newlines; TS strips one UTF-8 BOM.
            files[name] = {"sha256": hashlib.sha256(raw).hexdigest(),
                           "host_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
            if retained is not None:
                retained[name] = raw
        result.update(
            status="captured", library_root=library_root.as_posix(),
            installed_module=module.as_posix(), installed_module_sha256=hashlib.sha256(module_raw).hexdigest(),
            compiler_version=version, files=files,
            manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
            lockfile_sha256=hashlib.sha256(lock_raw).hexdigest(),
            package_sha256=hashlib.sha256(package_raw).hexdigest(),
        )
        result["inventory_sha256"] = hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        if retained is not None:
            capsule = _CompilerLibraryCapsule(_COMPILER_LIBRARY_CAPSULE_OWNER, result, package_raw, retained)
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        result["error"] = str(exc) or type(exc).__name__
    finally:
        if retained is not None:
            retained.clear()
    if capture_context is not None and result.get('status') == 'captured' and 'error' not in result:
        capture_context.update(status='CAPTURED_METADATA_ONLY', capture_policy=policy,
                               expected_origin={'collector_run_id': run_id,
                                                'inventory_sha256': result['inventory_sha256']})
    return result, capsule


def _reconcile_compiler_program_sources(
    data: dict[str, Any], inventory: dict[str, Any], matched_reads: set[str],
) -> tuple[list[str], list[str]]:
    """Match bounded external Program text with the owner-held installation image."""
    manifest = data.get("checked_source_manifest")
    context = data.get("semantic_context")
    if not isinstance(manifest, dict) or not isinstance(context, dict):
        return ["compiler_program_inventory_unavailable"], []
    files = manifest.get("files")
    outside = manifest.get("program_outside_workspace_files")
    count = manifest.get("program_outside_workspace_files_total")
    source_total = manifest.get("source_files_total")
    omitted = manifest.get("omitted_files")
    other_counts = [manifest.get(key) for key in (
        "program_project_source_files_total", "program_project_declaration_files_total",
        "program_workspace_external_files_total",
    )]
    project_root = data.get("project_root")
    workspace_root = context.get("workspace_root")
    if (manifest.get("version") != "v1"
            or manifest.get("hash_basis") != "sha256_utf8_source_text"
            or manifest.get("scope") != "semantic_context_not_snapshot_bound"
            or not isinstance(files, list) or any(not isinstance(row, dict) for row in files)
            or not isinstance(outside, list) or any(not isinstance(row, dict) for row in outside)
            or any(type(value) is not int or value < 0
                   for value in [count, source_total, omitted, *other_counts])
            or source_total != len(files) + omitted
            or source_total != count + sum(other_counts)
            or len(outside) > count
            or not isinstance(project_root, str) or not Path(project_root).is_absolute()
            or not isinstance(workspace_root, str) or not Path(workspace_root).is_absolute()
            or not Path(project_root).is_relative_to(Path(workspace_root))):
        return ["compiler_program_inventory_unavailable"], []
    errors: set[str] = set()
    if len(outside) != count:
        errors.add("compiler_program_inventory_incomplete")
    indexed: dict[str, list[dict[str, Any]]] = {}
    for row in files:
        name = row.get("file")
        if isinstance(name, str):
            indexed.setdefault(name, []).append(row)
    library_root = Path(inventory["library_root"])
    seen: set[str] = set()
    matched_program: set[str] = set()
    for entry in outside:
        candidate_text = entry.get("file")
        declaration = entry.get("declaration")
        if (not isinstance(candidate_text, str) or not candidate_text
                or "\\" in candidate_text or type(declaration) is not bool):
            errors.add("compiler_program_path_unbound")
            continue
        candidate = Path(candidate_text)
        if (not candidate.is_absolute() or ".." in candidate.parts
                or candidate.as_posix() != candidate_text
                or candidate.is_relative_to(Path(workspace_root))
                or candidate_text in seen):
            errors.add("compiler_program_path_unbound")
            continue
        seen.add(candidate_text)
        manifest_name = os.path.relpath(candidate, Path(project_root)).replace("\\", "/")
        rows = indexed.get(manifest_name, [])
        if len(rows) != 1:
            errors.add("compiler_program_manifest_unavailable")
            continue
        if not candidate.is_relative_to(library_root):
            errors.add("compiler_program_nonlibrary_input_unbound")
            continue
        relative = candidate.relative_to(library_root).as_posix()
        expected = inventory["files"].get(relative)
        digest = rows[0].get("sha256")
        if (not declaration or not relative.startswith("lib") or not relative.endswith(".d.ts")
                or not isinstance(expected, dict)
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or digest != expected.get("host_text_sha256")
                or relative not in matched_reads):
            errors.add(f"compiler_program_source_unbound:{relative}")
        else:
            matched_program.add(relative)
    if not matched_program:
        errors.add("compiler_program_files_unavailable")
    return sorted(errors), sorted(matched_program)


def reconcile_compiler_library_inputs(payload: Any, inventory: dict[str, Any]) -> dict[str, Any]:
    """Compare actual host reads with the separately held pre-invocation image.

    The caller passes its own inventory, not a child payload's authority claim.
    No path reported by the child is opened or resolved here.
    """
    result: dict[str, Any] = {
        "version": "v1", "status": "BLOCKED", "scope": "positive_compiler_library_reads_only",
        "producer": "tools.engines.react_frontier_intelligence",
        "collector_run_id": inventory.get("collector_run_id"),
        "inventory_sha256": inventory.get("inventory_sha256"),
        "inventory": inventory,
        "loaded_code_attested": False, "snapshot_bound": False, "projects": {},
    }
    if inventory.get("status") != "captured":
        result["errors"] = ["compiler_library_inventory_unavailable"]
        return result
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if (not isinstance(meta, dict) or not meta.get("collector_run_id")
            or meta["collector_run_id"] != inventory.get("collector_run_id")
            or payload.get("collector_status") != "OK"):
        result["errors"] = ["compiler_library_invocation_mismatch"]
        return result
    projects = payload.get("projects")
    if not isinstance(projects, dict) or not projects:
        result["errors"] = ["compiler_library_project_scope_unavailable"]
        return result
    errors: set[str] = set()
    root = Path(inventory["library_root"])
    for project, data in projects.items():
        context = data.get("semantic_context") if isinstance(data, dict) else None
        if (not isinstance(context, dict) or data.get("mode") != "semantic" or data.get("status") != "OK"
                or context.get("observations_complete") is not True):
            errors.add(f"{project}:compiler_library_observations_unavailable")
            continue
        compiler = context.get("compiler")
        if (not isinstance(compiler, dict)
                or compiler.get("module_hash_basis") != "installed_entry_bytes_read_before_analysis"
                or compiler.get("version") != inventory["compiler_version"]
                or any(compiler.get(key) != inventory[key] for key in
                       ("library_root", "installed_module", "installed_module_sha256"))):
            errors.add(f"{project}:compiler_installation_identity_mismatch")
            continue
        rows = context.get("observations")
        if not isinstance(rows, list):
            errors.add(f"{project}:compiler_library_observations_unavailable")
            continue
        matched: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                errors.add(f"{project}:compiler_library_observations_unavailable")
                continue
            candidate = row.get("path")
            in_root = isinstance(candidate, str) and Path(candidate).is_relative_to(root)
            if row.get("operation") != "readFile" or not (in_root or row.get("input_scope") == "compiler_library"):
                continue
            relative = Path(candidate).relative_to(root).as_posix() if in_root else ""
            expected = inventory["files"].get(relative)
            value = row.get("value")
            if (not in_root or not expected or ".." in Path(candidate).parts
                    or row.get("input_scope") != "compiler_library" or row.get("stage") != "compiler"
                    or row.get("allowed") is not True or row.get("access_status") != "present"
                    or row.get("error_code") or row.get("filtered_entries", 0) != 0
                    or not isinstance(value, dict) or value.get("text_sha256") != expected["host_text_sha256"]):
                errors.add(f"{project}:compiler_library_read_unbound")
            else:
                matched.add(relative)
        # An empty trace is not a positive library-read proof (e.g. noLib).
        if not matched:
            errors.add(f"{project}:compiler_library_reads_unavailable")
        program_errors, matched_program = _reconcile_compiler_program_sources(data, inventory, matched)
        errors.update(f"{project}:{issue}" for issue in program_errors)
        result["projects"][project] = {
            "matched_files": sorted(matched), "matched_program_files": matched_program,
        }
    result["errors"] = sorted(errors)
    result["status"] = "MATCHED_POSITIVE_READS" if not errors else "BLOCKED"
    return result


def _reserved_component(name: str) -> bool:
    if hasattr(ntpath, "isreserved"):
        return ntpath.isreserved(name)
    return PureWindowsPath(name).is_reserved()  # Python 3.11/3.12 compatibility.


def _resolution_snapshot(context: Any, project_atlas: Any) -> dict[str, Any] | None:
    """Validate the shared captured namespace before any lookup comparison."""
    if not isinstance(context, dict) or not isinstance(project_atlas, dict):
        return None
    inventory = project_atlas.get("typescript_resolution_inputs")
    metadata = project_atlas.get("project")
    if (not isinstance(inventory, dict) or not isinstance(metadata, dict)
            or inventory.get("version") != "v1"
            or inventory.get("scope") != "captured_directory_names_only"
            or inventory.get("path_semantics") not in {"windows_directory_names", "posix_directory_names"}
            or inventory.get("status") not in {"captured", "partial"}):
        return None
    roots = [metadata.get("root"), context.get("project_root"), inventory.get("project_root")]
    if any(not isinstance(value, str) or not value or not Path(value).is_absolute()
           or ".." in Path(value).parts for value in roots):
        return None
    root = Path(roots[0])
    if any(Path(value) != root for value in roots):
        return None
    directories = inventory.get("directories")
    policy = _binding_resolution_policy()
    if not isinstance(directories, dict) or len(directories) > policy["max_directories"]:
        return None
    members: dict[str, set[str]] = {}
    ambiguous_names: set[str] = set()
    entry_count = name_bytes = 0
    for relative, names in directories.items():
        if (not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative
                or relative.startswith("/") or ".." in PurePosixPath(relative).parts
                or str(PurePosixPath(relative)) != relative
                or not isinstance(names, list) or any(not valid_directory_name(name) for name in names)):
            return None
        entry_count += len(names)
        try:
            name_bytes += sum(len(name.encode("utf-8")) for name in names)
        except UnicodeError:
            return None
        if entry_count > policy["max_entries"] or name_bytes > policy["max_name_bytes"]:
            return None
        # Conservative on case-sensitive hosts too: a differently-cased existing
        # name prevents an absence claim rather than guessing filesystem policy.
        members[relative] = {name.casefold() for name in names}
        if any(not name.isascii() for name in names):
            ambiguous_names.add(relative)
    # Enumeration is not atomic. A later source/auxiliary read or child listing
    # may prove a name present that its earlier parent listing omitted. Reject
    # that internally inconsistent image instead of manufacturing absence.
    source_files = project_atlas.get("files", {})
    auxiliary = project_atlas.get("typescript_auxiliary_inputs", {})
    auxiliary_files = auxiliary.get("files", {}) if isinstance(auxiliary, dict) else None
    if not isinstance(source_files, dict) or not isinstance(auxiliary_files, dict):
        return None
    file_names = set(source_files) | set(auxiliary_files)
    if file_names.intersection(directories):
        return None  # An image cannot prove the same path both file and directory.
    for relative in (*directories, *source_files, *auxiliary_files):
        if (not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative
                or relative.startswith("/") or ".." in PurePosixPath(relative).parts
                or str(PurePosixPath(relative)) != relative):
            return None
        parent = PurePosixPath(".")
        for part in PurePosixPath(relative).parts:
            if parent.as_posix() in file_names:
                return None  # A captured file cannot also contain descendants.
            names = members.get(parent.as_posix())
            if names is None:
                break
            if part.casefold() not in names:
                return None
            parent /= part
    return {"root": root, "directories": directories, "members": members,
            "exact_members": {relative: set(names) for relative, names in directories.items()},
            "ambiguous_names": ambiguous_names, "path_semantics": inventory["path_semantics"]}


def _literal_lookup_parts(candidate: Any, root: Path) -> tuple[str, ...] | None:
    """Conservative lexical matching only; never resolve a live target path."""
    if (not isinstance(candidate, str) or not Path(candidate).is_absolute()
            or ".." in Path(candidate).parts):
        return None
    try:
        parts = Path(candidate).relative_to(root).parts
    except ValueError:
        return None
    if any(not valid_directory_name(part) or not part.isascii()
           or part.rstrip(" .") != part or _reserved_component(part)
           or any(ord(char) < 32 or char in '<>"|?*' for char in part) for part in parts):
        return None
    return parts


def _captured_file_content(relative: str, project_atlas: dict[str, Any], root: Path) -> bool:
    """A listed name is not file authority; require captured content identity."""
    source = project_atlas.get("files", {}).get(relative)
    if isinstance(source, dict) and _valid_atlas_source_hash(source.get("hash")):
        return True
    auxiliary = project_atlas.get("typescript_auxiliary_inputs", {})
    entry = auxiliary.get("files", {}).get(relative)
    return bool(auxiliary.get("version") == "v1" and auxiliary.get("status") in {"captured", "partial"}
                and auxiliary.get("scope") == "owned_walk_positive_inputs_only"
                and auxiliary.get("negative_resolution_authority") is False
                and auxiliary.get("project_root") == root.as_posix()
                and isinstance(entry, dict) and entry.get("status") == "ok"
                and isinstance(entry.get("host_text_sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", entry["host_text_sha256"]))


def _captured_canonical_auxiliary_file(relative: str, project_atlas: dict[str, Any], root: Path) -> bool:
    """Use the existing auxiliary read's resolved path, never a later lookup."""
    auxiliary = project_atlas.get("typescript_auxiliary_inputs", {})
    if not isinstance(auxiliary, dict):
        return False
    files = auxiliary.get("files")
    entry = files.get(relative) if isinstance(files, dict) else None
    return bool(auxiliary.get("version") == "v1" and auxiliary.get("status") in {"captured", "partial"}
                and auxiliary.get("scope") == "owned_walk_positive_inputs_only"
                and auxiliary.get("negative_resolution_authority") is False
                and auxiliary.get("project_root") == root.as_posix()
                and isinstance(entry, dict) and entry.get("status") == "ok"
                and entry.get("canonical_file_path") is True
                and isinstance(entry.get("host_text_sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", entry["host_text_sha256"]))


def _captured_canonical_source_file(relative: str, project_atlas: dict[str, Any]) -> bool:
    """Require the owned Atlas walk's source-path bit and captured text identity."""
    source = project_atlas.get("files", {}).get(relative)
    return bool(isinstance(source, dict)
                and source.get("language") in {"typescript", "javascript"}
                and source.get("canonical_file_path") is True
                and _valid_atlas_source_hash(source.get("hash")))


def _captured_directory_kinds(
    relative: str, snapshot: dict[str, Any], project_atlas: dict[str, Any],
) -> dict[str, Any] | None:
    """Project complete immediate kinds from existing captured authority only."""
    directories, root = snapshot["directories"], snapshot["root"]
    names = directories.get(relative)
    if names is None or len(names) != len({name.casefold() for name in names}):
        return None
    children, files = [], []
    for name in names:
        child = (PurePosixPath(relative) / name).as_posix()
        if _literal_lookup_parts((root / child).as_posix(), root) is None:
            return None
        if child in directories:
            children.append(name)
        elif not _captured_file_content(child, project_atlas, root):
            return None  # Excluded/aliased/unreadable name is not a known file.
        else:
            files.append(name)
    # TS returns sorted immediate basenames. ASCII-only names make Python's
    # ordering identical to its UTF-16 sort, without a second glob engine.
    def digest(entries):
        encoded = json.dumps(sorted(entries), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return {"entries": len(entries), "sha256": hashlib.sha256(encoded).hexdigest()}
    return {"entries": len(names), "files": digest(files), "directories": digest(children), "unsupported_entries": 0}


def _captured_child_directories(
    relative: str, snapshot: dict[str, Any], project_atlas: dict[str, Any],
) -> dict[str, Any] | None:
    kinds = _captured_directory_kinds(relative, snapshot, project_atlas)
    return kinds["directories"] if kinds is not None else None


def _canonical_captured_directory(candidate: Any, snapshot: dict[str, Any]) -> str | None:
    parts = _literal_lookup_parts(candidate, snapshot["root"])
    if parts is None or "." not in snapshot["directories"]:
        return None
    parent = PurePosixPath(".")
    for part in parts:
        if part not in snapshot["exact_members"].get(parent.as_posix(), set()):
            return None
        parent /= part
    relative = parent.as_posix()
    return relative if relative in snapshot["directories"] else None


def _recursive_directory_inputs_match(row, snapshot, project_atlas, projections) -> bool:
    """Compare observed glob inputs, not a second execution of glob semantics."""
    trace = row.get("recursive_inputs")
    if (not isinstance(trace, dict) or trace.get("version") != "v1"
            or trace.get("scope") != "actual_read_directory_calls" or trace.get("complete") is not True
            or type(trace.get("omitted_observations")) is not int or trace["omitted_observations"] != 0):
        return False
    inputs = trace.get("observations")
    if (not isinstance(inputs, list) or not inputs or type(trace.get("observed_calls")) is not int
            or trace["observed_calls"] != len(inputs)):
        return False
    value = row.get("value")
    if (not isinstance(value, dict) or type(value.get("entries")) is not int or value["entries"] < 0
            or not isinstance(value.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])):
        return False
    enumerated, resolved = set(), set()
    for observation in inputs:
        if not isinstance(observation, dict):
            return False
        relative = _canonical_captured_directory(observation.get("path"), snapshot)
        if relative is None:
            return False
        operation, value = observation.get("operation"), observation.get("value")
        if operation == "readdirSync":
            if relative not in projections:
                projections[relative] = _captured_directory_kinds(relative, snapshot, project_atlas)
            expected = projections[relative]
            if (expected is None or not isinstance(value, dict)
                    or any(type(value.get(key)) is not int for key in ("entries", "unsupported_entries"))
                    or any(not isinstance(value.get(key), dict) or type(value[key].get("entries")) is not int
                           for key in ("files", "directories"))
                    or value != expected):
                return False
            enumerated.add(relative)
        elif operation in {"realpathSync", "realpathSync.native"}:
            if _canonical_captured_directory(value, snapshot) != relative:
                return False
            resolved.add(relative)
        else:
            return False  # Link-following stat, legacy entries and other I/O are unknown.
    query_root = _canonical_captured_directory(row.get("path"), snapshot)
    return query_root in enumerated and enumerated == resolved


def semantic_positive_resolution_errors(context: Any, project_atlas: Any) -> list[str]:
    """Match positive existence and canonical-directory identity, not live state.

    Listed names alone do not prove entry type. File presence requires captured
    source/auxiliary content; directory presence requires its own canonical walk
    record. Lists require every immediate kind. Recursive queries additionally
    require complete actual enumeration/realpath inputs, not result digests alone.
    This compares input identities; it does not independently replay the compiler
    or establish native-project semantics, link binding or atomic stability.
    """
    unknown = ["positive_resolution_not_snapshot_bound"]
    observations = context.get("observations") if isinstance(context, dict) else None
    if (not isinstance(observations, list) or any(not isinstance(row, dict) for row in observations)
            or context.get("observations_complete") is False):
        return unknown
    operations = {"fileExists", "directoryExists", "getDirectories", "readDirectory", "realpath"}
    rows = [row for row in observations if isinstance(row, dict)
            and row.get("operation") in operations and row.get("access_status") != "missing"]
    if not rows:
        return []
    snapshot = _resolution_snapshot(context, project_atlas)
    if snapshot is None:
        return unknown
    root, directories = snapshot["root"], snapshot["directories"]
    exact_members = snapshot["exact_members"]
    empty_args = hashlib.sha256(b"[]").hexdigest()
    directory_results: dict[str, dict[str, Any] | None] = {}
    recursive_projections: dict[str, dict[str, Any] | None] = {}
    if any(row.get("operation") == "readDirectory" for row in rows):
        policy = _binding_lineage_policy()
        limit = policy["artifacts"]["ts_diagnostics"]["semantic_context"]["max_observations"]
        observed_limit = context.get("observation_limit")
        if (type(limit) is not int or type(observed_limit) is not int
                or not 0 < observed_limit <= limit or context.get("observations_complete") is not True):
            return unknown
        units = len(observations)
        for row in observations:
            trace = row.get("recursive_inputs")
            if trace is not None:
                if not isinstance(trace, dict) or not isinstance(trace.get("observations"), list):
                    return unknown
                units += len(trace["observations"])
            if units > observed_limit:
                return unknown
    for row in rows:
        if (row.get("allowed") is not True or row.get("input_scope") != "workspace"
                or row.get("stage") not in {"config", "compiler"}
                or row.get("access_status") != "present"
                or row.get("error_code") or row.get("filtered_entries", 0) != 0
                or row.get("io_failure") is not None):
            return unknown
        if row.get("operation") == "readDirectory":
            digest = row.get("arguments_sha256")
            if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or not _recursive_directory_inputs_match(row, snapshot, project_atlas, recursive_projections)):
                return unknown
            continue
        if row.get("arguments_sha256") != empty_args:
            return unknown
        parts = _literal_lookup_parts(row.get("path"), root)
        if parts is None or "." not in directories:
            return unknown
        parent = PurePosixPath(".")
        for part in parts:
            # Exact spelling and captured canonical ancestors, not casefold or
            # display-name aliases, establish this restricted identity.
            if part not in exact_members.get(parent.as_posix(), set()):
                return unknown
            parent /= part
        relative = parent.as_posix()
        operation, value = row["operation"], row.get("value")
        if operation == "directoryExists" and value is True and relative in directories:
            continue
        if operation == "getDirectories" and isinstance(value, dict):
            if relative not in directory_results:
                directory_results[relative] = _captured_child_directories(relative, snapshot, project_atlas)
            expected = directory_results[relative]
            if (expected is not None and type(value.get("entries")) is int
                    and value.get("entries") == expected["entries"]
                    and value.get("sha256") == expected["sha256"]):
                continue
        if operation == "realpath" and relative in directories:
            resolved_parts = _literal_lookup_parts(value, root)
            if resolved_parts == parts:
                continue  # Agreement with captured canonical directory, not an I/O success claim.
        if operation == "realpath" and relative not in directories:
            resolved_parts = _literal_lookup_parts(value, root)
            if resolved_parts == parts and (
                _captured_canonical_auxiliary_file(relative, project_atlas, root)
                or _captured_canonical_source_file(relative, project_atlas)
            ):
                continue  # Captured literal file identity, not atomic link stability.
        if operation == "fileExists" and value is True and relative not in directories:
            if _captured_file_content(relative, project_atlas, root):
                continue
        return unknown
    return []


def semantic_negative_resolution_errors(context: Any, project_atlas: Any) -> list[str]:
    """Reconcile missing probes with enumeration-time names, never live paths."""
    unknown = ["negative_resolution_not_snapshot_bound"]
    observations = context.get("observations") if isinstance(context, dict) else None
    if not isinstance(observations, list) or any(not isinstance(row, dict) for row in observations):
        return unknown
    missing = [row for row in observations if isinstance(row, dict) and row.get("access_status") == "missing"]
    if not missing:
        return []
    snapshot = _resolution_snapshot(context, project_atlas)
    if snapshot is None:
        return unknown
    root, members = snapshot["root"], snapshot["members"]
    ambiguous_names = snapshot["ambiguous_names"]
    for row in missing:
        operation, value, candidate = row.get("operation"), row.get("value"), row.get("path")
        if (row.get("allowed") is not True or row.get("input_scope") != "workspace"
                or row.get("error_code") or row.get("filtered_entries", 0) != 0
                or not ((operation in {"fileExists", "directoryExists"} and value is False)
                        or (operation == "readFile" and value is None))
                or not isinstance(candidate, str) or not Path(candidate).is_absolute()
                or ".." in Path(candidate).parts):
            return unknown
        parts = _literal_lookup_parts(candidate, root)
        if parts is None:
            return unknown
        parent = PurePosixPath(".")
        for part in parts:
            names = members.get(parent.as_posix())
            if names is None:
                return unknown  # Excluded, unreadable, aliased or omitted directory.
            if part.casefold() not in names:
                if parent.as_posix() in ambiguous_names:
                    return unknown  # Filesystem Unicode equivalence is not captured.
                pieces = part.split(".")
                if (snapshot["path_semantics"] == "windows_directory_names" and names
                        and len(pieces) <= 2 and len(pieces[0]) <= 8
                        and (len(pieces) == 1 or len(pieces[1]) <= 3)):
                    return unknown  # A directory listing does not enumerate 8.3 aliases.
                break  # This exact directory listing proves the next name absent.
            parent /= part
        else:
            return unknown  # Every queried component existed in the Atlas listing.
    return []


def semantic_auxiliary_input_errors(context: Any, project_atlas: Any) -> list[str]:
    """Compare positive host reads with ingestion identity, never infer absence."""
    if not isinstance(context, dict) or not isinstance(project_atlas, dict):
        return ["auxiliary_input_inventory_unavailable"]
    inventory = project_atlas.get("typescript_auxiliary_inputs")
    metadata = project_atlas.get("project") or {}
    root = metadata.get("root") if isinstance(metadata, dict) else None
    if not root or not isinstance(inventory, dict) or inventory.get("version") != "v1":
        return ["auxiliary_input_inventory_unavailable"]
    roots = [root, context.get("project_root"), inventory.get("project_root")]
    if any(not isinstance(value, str) or not value or not Path(value).is_absolute()
           or ".." in Path(value).parts for value in roots):
        return ["auxiliary_input_root_mismatch"]
    root = Path(root)
    if any(root != Path(value) for value in roots):
        return ["auxiliary_input_root_mismatch"]
    if (inventory.get("status") not in {"captured", "partial"}
            or inventory.get("scope") != "owned_walk_positive_inputs_only"
            or inventory.get("negative_resolution_authority") is not False):
        return ["auxiliary_input_inventory_unavailable"]
    observations = context.get("observations")
    entries = inventory.get("files")
    if not isinstance(observations, list) or not isinstance(entries, dict):
        return ["auxiliary_input_inventory_unavailable"]
    suffixes = tuple(_binding_auxiliary_policy()["file_suffixes"])
    errors: set[str] = set()
    for row in observations:
        if not isinstance(row, dict) or row.get("operation") != "readFile":
            continue
        value = row.get("value")
        candidate = row.get("path")
        if not isinstance(value, dict) or not isinstance(candidate, str) or not candidate.lower().endswith(suffixes):
            continue
        if row.get("input_scope") == "compiler_library":
            # A collector scope tag cannot authorize itself. Keep toolchain
            # observations separate from project inputs and explicitly unbound.
            errors.add("compiler_library_not_snapshot_bound")
            continue
        # Lexical normalization only: evidence consumers must not consult live
        # realpaths, which may have changed since the actual compiler read.
        candidate_path = Path(candidate)
        if not candidate_path.is_absolute() or ".." in candidate_path.parts:
            errors.add("unsafe_auxiliary_input_path")
            continue
        try:
            relative = candidate_path.relative_to(root).as_posix()
        except ValueError:
            errors.add("auxiliary_input_outside_project")
            continue  # Includes compiler libraries; their authority is separate.
        entry = entries.get(relative)
        digest = value.get("text_sha256")
        if (row.get("allowed") is not True or not isinstance(entry, dict)
                or entry.get("status") != "ok" or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or entry.get("host_text_sha256") != digest):
            errors.add(f"auxiliary_input_unbound:{relative}")
    return sorted(errors)


def semantic_workspace_read_errors(context: Any, project_atlas: Any) -> list[str]:
    """Bind positive compiler/config host text reads to captured Atlas content.

    This is input correspondence only. It does not prove file realpaths,
    compiler option semantics, project references or one atomic filesystem
    image, and cannot by itself authorize a semantic lineage receipt.
    """
    unknown = ["workspace_read_inventory_unavailable"]
    if not isinstance(context, dict) or not isinstance(project_atlas, dict):
        return unknown
    observations = context.get("observations")
    metadata = project_atlas.get("project")
    root_text = metadata.get("root") if isinstance(metadata, dict) else None
    observed_root = context.get("project_root")
    if (context.get("observations_complete") is not True
            or not isinstance(observations, list)
            or any(not isinstance(row, dict) for row in observations)
            or not isinstance(root_text, str) or not root_text
            or not Path(root_text).is_absolute() or ".." in Path(root_text).parts
            or not isinstance(observed_root, str) or not observed_root
            or Path(observed_root) != Path(root_text)):
        return unknown
    sources = project_atlas.get("files")
    auxiliary = project_atlas.get("typescript_auxiliary_inputs")
    auxiliary_files = auxiliary.get("files") if isinstance(auxiliary, dict) else {}
    if not isinstance(sources, dict) or not isinstance(auxiliary_files, dict):
        return unknown
    root = Path(root_text)
    errors: set[str] = set()
    positive_reads = 0
    for row in observations:
        if row.get("operation") != "readFile" or row.get("access_status") == "missing":
            continue
        if row.get("input_scope") == "compiler_library":
            continue  # Separately reconciled with the producer-owned installation image.
        positive_reads += 1
        parts = _literal_lookup_parts(row.get("path"), root)
        if parts is None or not parts:
            errors.add("workspace_read_path_unbound")
            continue
        relative = PurePosixPath(*parts).as_posix()
        value = row.get("value")
        digest = value.get("text_sha256") if isinstance(value, dict) else None
        if (row.get("input_scope") != "workspace" or row.get("allowed") is not True
                or row.get("stage") not in {"config", "compiler"}
                or row.get("access_status") != "present" or row.get("error_code")
                or row.get("filtered_entries", 0) != 0 or row.get("io_failure") is not None
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            errors.add(f"workspace_read_unbound:{relative}")
            continue
        source = sources.get(relative)
        captured = source.get("hash") if isinstance(source, dict) else None
        entry = auxiliary_files.get(relative)
        if entry is not None:
            if (auxiliary.get("version") != "v1"
                    or auxiliary.get("status") not in {"captured", "partial"}
                    or auxiliary.get("scope") != "owned_walk_positive_inputs_only"
                    or auxiliary.get("negative_resolution_authority") is not False
                    or auxiliary.get("project_root") != root.as_posix()
                    or not isinstance(entry, dict) or entry.get("status") != "ok"):
                errors.add(f"workspace_read_unbound:{relative}")
                continue
            auxiliary_digest = entry.get("host_text_sha256")
            if (not isinstance(auxiliary_digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", auxiliary_digest)
                    or auxiliary_digest != digest):
                errors.add(f"workspace_read_content_mismatch:{relative}")
        if source is not None:
            if not _atlas_source_hash_matches(captured, {
                "md5": value.get("text_md5"), "sha256": digest,
            }):
                errors.add(f"workspace_read_content_mismatch:{relative}")
        elif entry is None:
            errors.add(f"workspace_read_unbound:{relative}")
    if not positive_reads:
        errors.add("workspace_positive_reads_unavailable")
    return sorted(errors)


def semantic_config_root_read_errors(data: Any, project_atlas: Any) -> list[str]:
    """Require the contract-selected root config's actual read and Atlas text.

    This checks one input observation, not the complete extends graph, parsed
    option semantics or the native compiler profile.
    """
    unknown = ["semantic_config_root_read_unbound"]
    if not isinstance(data, dict) or not isinstance(project_atlas, dict):
        return unknown
    metadata = project_atlas.get("project")
    root_text = metadata.get("root") if isinstance(metadata, dict) else None
    relative = data.get("tsconfig")
    context = data.get("semantic_context")
    try:
        policy = _binding_lineage_policy()
        root_config_file = policy["artifacts"]["ts_diagnostics"]["root_config_filename"]
    except (OSError, ValueError, KeyError, TypeError):
        return unknown
    if (not isinstance(root_text, str) or not Path(root_text).is_absolute()
            or not isinstance(relative, str) or not relative
            or not isinstance(root_config_file, str) or relative != root_config_file
            or relative.startswith("/") or "\\" in relative or ":" in relative
            or "/" in relative
            or ".." in PurePosixPath(relative).parts
            or str(PurePosixPath(relative)) != relative
            or PurePosixPath(relative).suffix.lower() not in {".json", ".jsonc"}
            or not isinstance(context, dict)
            or context.get("observations_complete") is not True
            or not isinstance(context.get("observations"), list)
            or any(not isinstance(row, dict) for row in context["observations"])
            or not isinstance(data.get("project_root"), str)
            or Path(data["project_root"]) != Path(root_text)
            or not isinstance(context.get("project_root"), str)
            or Path(context["project_root"]) != Path(root_text)):
        return unknown
    root = Path(root_text)
    expected = (root / PurePosixPath(relative)).as_posix()
    if _literal_lookup_parts(expected, root) != PurePosixPath(relative).parts:
        return unknown
    reads = [row for row in context["observations"]
             if row.get("stage") == "config" and row.get("operation") == "readFile"
             and row.get("path") == expected]
    if len(reads) != 1:
        return unknown
    scoped = {"project_root": root_text, "observations_complete": True, "observations": reads}
    if (semantic_auxiliary_input_errors(scoped, project_atlas)
            or semantic_workspace_read_errors(scoped, project_atlas)):
        return unknown
    return []


def semantic_diagnostic_source_errors(data: Any, project_atlas: Any) -> list[str]:
    """Compare emitted diagnostic and selected-root SourceFile text to Atlas.

    Cross-check the selected-root context digest against that same manifest;
    neither producer field alone can establish an independent config snapshot.
    The collector's SourceFile text and positive host reads are distinct inputs.
    Even a match here cannot establish the whole Program or one atomic image.
    """
    unavailable = ["semantic_checked_manifest_unavailable"]
    if not isinstance(data, dict) or not isinstance(project_atlas, dict):
        return unavailable
    manifest = data.get("checked_source_manifest")
    metadata = project_atlas.get("project")
    root = metadata.get("root") if isinstance(metadata, dict) else None
    observed_root = data.get("project_root")
    files = manifest.get("files") if isinstance(manifest, dict) else None
    selected_roots = manifest.get("selected_root_files") if isinstance(manifest, dict) else None
    diagnostics = data.get("diagnostics")
    summary = data.get("summary")
    atlas_files = project_atlas.get("files")
    if (data.get("status") != "OK" or data.get("mode") != "semantic"
            or not isinstance(manifest, dict) or manifest.get("version") != "v1"
            or manifest.get("hash_basis") != "sha256_utf8_source_text"
            or manifest.get("scope") != "semantic_context_not_snapshot_bound"
            or not isinstance(files, list) or not isinstance(selected_roots, list)
            or not isinstance(diagnostics, list) or not isinstance(summary, dict)
            or any(not isinstance(name, str) for name in selected_roots)
            or len(selected_roots) != len(set(selected_roots))
            or summary.get("root_files_checked") != len(selected_roots)
            or not isinstance(atlas_files, dict)
            or not isinstance(root, str) or not root or not Path(root).is_absolute()
            or ".." in Path(root).parts
            or not isinstance(observed_root, str) or Path(observed_root) != Path(root)
            or type(manifest.get("source_files_total")) is not int
            or type(manifest.get("omitted_files")) is not int
            or manifest["omitted_files"] < 0
            or manifest["source_files_total"] != len(files) + manifest["omitted_files"]
            or any(not isinstance(row, dict) for row in files + diagnostics)
            or any(not isinstance(row.get("file"), str) for row in diagnostics)):
        return unavailable
    relevant = {name: "root" for name in selected_roots}
    relevant.update({row["file"]: "diagnostic" for row in diagnostics if row.get("file")})
    errors: set[str] = set()
    context = data.get("semantic_context")
    if not isinstance(context, dict):
        errors.add("semantic_selected_roots_context_unavailable")
    else:
        selected_count = context.get("selected_roots")
        configured_count = context.get("configured_roots")
        selected_digest = context.get("selected_roots_sha256")
        configured_digest = context.get("configured_roots_sha256")
        if (type(selected_count) is not int or type(configured_count) is not int
                or selected_count < 0 or configured_count < 0
                or not isinstance(selected_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", selected_digest)
                or not isinstance(configured_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", configured_digest)):
            errors.add("semantic_selected_roots_context_unavailable")
        elif all(name and "\\" not in name and ":" not in name and not name.startswith("/")
                 and ".." not in PurePosixPath(name).parts
                 and str(PurePosixPath(name)) == name for name in selected_roots):
            absolute_roots = [(Path(root) / PurePosixPath(name)).as_posix() for name in selected_roots]
            digest = hashlib.sha256(json.dumps(
                absolute_roots, ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            if (selected_count != len(selected_roots) or configured_count < selected_count
                    or selected_digest != digest):
                errors.add("semantic_selected_roots_context_mismatch")
            if configured_count == selected_count and configured_digest != selected_digest:
                errors.add("semantic_configured_roots_context_mismatch")
    if not relevant:
        return sorted(errors)  # Empty inventory still cannot authorize lineage.
    indexed: dict[str, list[dict[str, Any]]] = {}
    for row in files:
        name = row.get("file")
        if isinstance(name, str) and name in relevant:
            indexed.setdefault(name, []).append(row)
    for name, kind in relevant.items():
        if (not isinstance(name, str) or not name or "\\" in name or ":" in name
                or name.startswith("/") or ".." in PurePosixPath(name).parts
                or str(PurePosixPath(name)) != name):
            errors.add(f"semantic_{kind}_file_path_unbound")
            continue
        matches = indexed.get(name, [])
        if len(matches) != 1:
            errors.add(f"semantic_{kind}_manifest_{'ambiguous' if matches else 'missing'}:{name}")
            continue
        atlas_row = atlas_files.get(name)
        atlas_digest = atlas_row.get("hash") if isinstance(atlas_row, dict) else None
        if not _valid_atlas_source_hash(atlas_digest):
            errors.add(f"semantic_{kind}_source_unbound:{name}")
        elif not _atlas_source_hash_matches(atlas_digest, matches[0]):
            errors.add(f"semantic_{kind}_source_mismatch:{name}")
    return sorted(errors)


def semantic_program_source_errors(data: Any, project_atlas: Any) -> list[str]:
    """Compare recorded project-local non-declaration Program files to Atlas.

    The collector's count is an observation, not independent proof of the
    entire Program. Even complete correspondence here cannot bind options,
    compiler libraries, excluded dependencies, physical links or one atomic
    filesystem image; semantic lineage remains blocked elsewhere.
    """
    unavailable = ["semantic_program_source_inventory_unavailable"]
    if not isinstance(data, dict) or not isinstance(project_atlas, dict):
        return unavailable
    manifest = data.get("checked_source_manifest")
    atlas_files = project_atlas.get("files")
    files = manifest.get("files") if isinstance(manifest, dict) else None
    names = manifest.get("program_project_source_files") if isinstance(manifest, dict) else None
    total = manifest.get("program_project_source_files_total") if isinstance(manifest, dict) else None
    source_total = manifest.get("source_files_total") if isinstance(manifest, dict) else None
    if (data.get("status") != "OK" or data.get("mode") != "semantic"
            or not isinstance(manifest, dict) or manifest.get("version") != "v1"
            or manifest.get("hash_basis") != "sha256_utf8_source_text"
            or manifest.get("scope") != "semantic_context_not_snapshot_bound"
            or not isinstance(files, list) or not isinstance(names, list)
            or not isinstance(atlas_files, dict)
            or type(total) is not int or type(source_total) is not int
            or total < 0 or total > source_total or len(names) > total
            or any(not isinstance(row, dict) for row in files)
            or any(not isinstance(name, str) for name in names)
            or len(names) != len(set(names))):
        return unavailable
    errors: set[str] = set()
    if total == 0:
        errors.add("semantic_program_source_inventory_empty")
    elif len(names) != total:
        errors.add("semantic_program_source_inventory_incomplete")
    indexed: dict[str, list[dict[str, Any]]] = {}
    for row in files:
        name = row.get("file")
        if isinstance(name, str) and name in names:
            indexed.setdefault(name, []).append(row)
    for name in names:
        if (not name or "\\" in name or ":" in name or name.startswith("/")
                or ".." in PurePosixPath(name).parts
                or str(PurePosixPath(name)) != name):
            errors.add("semantic_program_file_path_unbound")
            continue
        matches = indexed.get(name, [])
        if len(matches) != 1:
            errors.add(f"semantic_program_manifest_{'ambiguous' if matches else 'missing'}:{name}")
            continue
        atlas_row = atlas_files.get(name)
        atlas_digest = atlas_row.get("hash") if isinstance(atlas_row, dict) else None
        if not _valid_atlas_source_hash(atlas_digest):
            errors.add(f"semantic_program_source_unbound:{name}")
        elif not _atlas_source_hash_matches(atlas_digest, matches[0]):
            errors.add(f"semantic_program_source_mismatch:{name}")
    return sorted(errors)


def semantic_program_declaration_errors(data: Any, project_atlas: Any) -> list[str]:
    """Match captured project-local declaration SourceFile text to Atlas.

    This compares only manifest-covered declarations. Their observed total
    cannot independently prove complete Program or physical input authority.
    """
    unavailable = ["semantic_program_declaration_inventory_unavailable"]
    if not isinstance(data, dict) or not isinstance(project_atlas, dict):
        return unavailable
    manifest = data.get("checked_source_manifest")
    files = manifest.get("files") if isinstance(manifest, dict) else None
    names = manifest.get("program_project_declaration_files") if isinstance(manifest, dict) else None
    total = manifest.get("program_project_declaration_files_total") if isinstance(manifest, dict) else None
    source_total = manifest.get("source_files_total") if isinstance(manifest, dict) else None
    project_source_total = manifest.get("program_project_source_files_total") if isinstance(manifest, dict) else None
    metadata = project_atlas.get("project")
    root = metadata.get("root") if isinstance(metadata, dict) else None
    observed_root = data.get("project_root")
    if (data.get("status") != "OK" or data.get("mode") != "semantic"
            or not isinstance(manifest, dict) or manifest.get("version") != "v1"
            or manifest.get("hash_basis") != "sha256_utf8_source_text"
            or manifest.get("scope") != "semantic_context_not_snapshot_bound"
            or not isinstance(files, list) or not isinstance(names, list)
            or type(total) is not int or type(source_total) is not int
            or type(project_source_total) is not int
            or total < 0 or project_source_total < 0
            or total + project_source_total > source_total or len(names) > total
            or any(not isinstance(row, dict) for row in files)
            or any(not isinstance(name, str) for name in names)
            or len(names) != len(set(names))
            or not isinstance(root, str) or not root or not Path(root).is_absolute()
            or ".." in Path(root).parts
            or not isinstance(observed_root, str) or ".." in Path(observed_root).parts
            or Path(observed_root) != Path(root)):
        return unavailable
    if total == 0:
        return []
    errors: set[str] = set()
    if len(names) != total:
        errors.add("semantic_program_declaration_inventory_incomplete")
    auxiliary = project_atlas.get("typescript_auxiliary_inputs")
    entries = auxiliary.get("files") if isinstance(auxiliary, dict) else None
    if (not isinstance(auxiliary, dict) or auxiliary.get("version") != "v1"
            or auxiliary.get("status") not in {"captured", "partial"}
            or auxiliary.get("scope") != "owned_walk_positive_inputs_only"
            or auxiliary.get("negative_resolution_authority") is not False
            or auxiliary.get("project_root") != Path(root).as_posix()
            or not isinstance(entries, dict)):
        errors.add("semantic_program_declaration_auxiliary_unavailable")
        entries = {}
    indexed: dict[str, list[dict[str, Any]]] = {}
    for row in files:
        name = row.get("file")
        if isinstance(name, str) and name in names:
            indexed.setdefault(name, []).append(row)
    for name in names:
        if (not name or "\\" in name or ":" in name or name.startswith("/")
                or ".." in PurePosixPath(name).parts
                or str(PurePosixPath(name)) != name
                or not name.lower().endswith((".d.ts", ".d.mts", ".d.cts"))):
            errors.add("semantic_program_declaration_path_unbound")
            continue
        matches = indexed.get(name, [])
        if len(matches) != 1:
            errors.add(f"semantic_program_declaration_manifest_{'ambiguous' if matches else 'missing'}:{name}")
            continue
        entry = entries.get(name)
        digest = entry.get("host_text_sha256") if isinstance(entry, dict) else None
        if (not isinstance(entry, dict) or entry.get("status") != "ok"
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            errors.add(f"semantic_program_declaration_unbound:{name}")
        elif matches[0].get("sha256") != digest:
            errors.add(f"semantic_program_declaration_mismatch:{name}")
    return sorted(errors)


def semantic_workspace_external_errors(
    data: Any, atlas: dict[str, Any], project: str,
) -> list[str]:
    """Compare bounded out-of-project Program text only to a unique Atlas owner.

    This is lexical, cross-project content correspondence. The collector's
    list/count and workspace root cannot prove complete Program membership,
    physical ownership, or semantic lineage.
    """
    unavailable = ["semantic_workspace_external_inventory_unavailable"]
    if not isinstance(data, dict) or not isinstance(atlas, dict):
        return unavailable
    current = atlas.get(project)
    metadata = current.get("project") if isinstance(current, dict) else None
    root_text = metadata.get("root") if isinstance(metadata, dict) else None
    context = data.get("semantic_context")
    workspace_text = context.get("workspace_root") if isinstance(context, dict) else None
    manifest = data.get("checked_source_manifest")
    files = manifest.get("files") if isinstance(manifest, dict) else None
    externals = manifest.get("program_workspace_external_files") if isinstance(manifest, dict) else None
    total = manifest.get("program_workspace_external_files_total") if isinstance(manifest, dict) else None
    source_total = manifest.get("source_files_total") if isinstance(manifest, dict) else None
    local_source_total = manifest.get("program_project_source_files_total") if isinstance(manifest, dict) else None
    local_declaration_total = (
        manifest.get("program_project_declaration_files_total") if isinstance(manifest, dict) else None
    )
    if (data.get("status") != "OK" or data.get("mode") != "semantic"
            or not isinstance(manifest, dict) or manifest.get("version") != "v1"
            or manifest.get("hash_basis") != "sha256_utf8_source_text"
            or manifest.get("scope") != "semantic_context_not_snapshot_bound"
            or not isinstance(files, list) or any(not isinstance(row, dict) for row in files)
            or not isinstance(externals, list) or any(not isinstance(row, dict) for row in externals)
            or any(not isinstance(row.get("file"), str)
                   or type(row.get("declaration")) is not bool for row in externals)
            or len({row["file"] for row in externals}) != len(externals)
            or any(type(value) is not int or value < 0 for value in
                   (total, source_total, local_source_total, local_declaration_total))
            or total + local_source_total + local_declaration_total > source_total
            or len(externals) > total
            or not isinstance(root_text, str) or not Path(root_text).is_absolute()
            or ".." in Path(root_text).parts
            or not isinstance(workspace_text, str) or not Path(workspace_text).is_absolute()
            or ".." in Path(workspace_text).parts
            or not isinstance(data.get("project_root"), str)
            or Path(data["project_root"]) != Path(root_text)
            or not Path(root_text).is_relative_to(Path(workspace_text))):
        return unavailable
    if total == 0:
        return []
    errors: set[str] = set()
    if len(externals) != total:
        errors.add("semantic_workspace_external_inventory_incomplete")
    root = Path(root_text)
    workspace = Path(workspace_text)
    indexed: dict[str, list[dict[str, Any]]] = {}
    for row in files:
        name = row.get("file")
        if isinstance(name, str):
            indexed.setdefault(name, []).append(row)
    for external in externals:
        raw = external["file"]
        candidate = Path(raw)
        if (not raw or "\\" in raw or not candidate.is_absolute()
                or ".." in candidate.parts or candidate.as_posix() != raw
                or not candidate.is_relative_to(workspace)
                or candidate.is_relative_to(root)):
            errors.add("semantic_workspace_external_path_unbound")
            continue
        manifest_name = os.path.relpath(candidate, root).replace("\\", "/")
        matches = indexed.get(manifest_name, [])
        if len(matches) != 1:
            errors.add("semantic_workspace_external_manifest_unavailable")
            continue
        owners: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
        for owner, payload in atlas.items():
            if owner in {project, "symbols"} or not isinstance(payload, dict):
                continue
            project_meta = payload.get("project")
            owner_root_text = project_meta.get("root") if isinstance(project_meta, dict) else None
            if (not isinstance(owner_root_text, str) or not Path(owner_root_text).is_absolute()
                    or ".." in Path(owner_root_text).parts):
                continue
            owner_root = Path(owner_root_text)
            if not candidate.is_relative_to(owner_root):
                continue
            relative = candidate.relative_to(owner_root).as_posix()
            inventory = payload.get("typescript_auxiliary_inputs") if external["declaration"] else payload.get("files")
            entries = inventory.get("files") if external["declaration"] and isinstance(inventory, dict) else inventory
            if isinstance(entries, dict) and isinstance(entries.get(relative), dict):
                owners.append((str(owner), relative, entries[relative], payload))
        if not owners:
            errors.add("semantic_workspace_external_owner_unavailable")
            continue
        if len(owners) != 1:
            errors.add("semantic_workspace_external_owner_ambiguous")
            continue
        owner, relative, entry, payload = owners[0]
        if external["declaration"]:
            auxiliary = payload.get("typescript_auxiliary_inputs")
            digest = entry.get("host_text_sha256")
            if (not relative.lower().endswith((".d.ts", ".d.mts", ".d.cts"))
                    or not isinstance(auxiliary, dict) or auxiliary.get("version") != "v1"
                    or auxiliary.get("status") not in {"captured", "partial"}
                    or auxiliary.get("scope") != "owned_walk_positive_inputs_only"
                    or auxiliary.get("negative_resolution_authority") is not False
                    or auxiliary.get("project_root") != Path(payload["project"]["root"]).as_posix()
                    or entry.get("status") != "ok"
                    or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                errors.add(f"semantic_workspace_external_unbound:{owner}:{relative}")
            elif matches[0].get("sha256") != digest:
                errors.add(f"semantic_workspace_external_mismatch:{owner}:{relative}")
        else:
            digest = entry.get("hash")
            if not _valid_atlas_source_hash(digest):
                errors.add(f"semantic_workspace_external_unbound:{owner}:{relative}")
            elif not _atlas_source_hash_matches(digest, matches[0]):
                errors.add(f"semantic_workspace_external_mismatch:{owner}:{relative}")
    return sorted(errors)


def _append_semantic_errors(errors, prefix, data, atlas, project):
    """One canonical semantic reconciliation path; never grants admission."""
    errors.append(prefix + "semantic_context_not_snapshot_bound")
    context = data.get("semantic_context")
    if not isinstance(context, dict) or context.get("observations_complete") is not True:
        errors.append(prefix + "semantic_context_observation_incomplete")
    # These producer observations can prove a profile difference, never
    # prove that the native options/references match an Atlas snapshot.
    options_hash = context.get("requested_options_sha256") if isinstance(context, dict) else None
    effective_options = context.get("effective_options") if isinstance(context, dict) else None
    overrides = context.get("overridden_options") if isinstance(context, dict) else None
    if (not isinstance(options_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", options_hash)
            or not isinstance(effective_options, dict)
            or not isinstance(overrides, list)
            or any(not isinstance(option, str) or not option for option in overrides)
            or overrides != sorted(set(overrides))):
        errors.append(prefix + "semantic_compiler_profile_unavailable")
    elif overrides:
        errors.append(prefix + "semantic_compiler_profile_overridden")
    reference_count = context.get("project_references") if isinstance(context, dict) else None
    reference_hash = context.get("project_references_sha256") if isinstance(context, dict) else None
    if (type(reference_count) is not int or reference_count < 0
            or not isinstance(reference_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", reference_hash)):
        errors.append(prefix + "semantic_project_references_unavailable")
    elif reference_count:
        errors.append(prefix + "semantic_project_references_unloaded")
    elif reference_hash != hashlib.sha256(b"[]").hexdigest():
        errors.append(prefix + "semantic_project_references_context_mismatch")
    observations = context.get("observations") if isinstance(context, dict) else None
    for row in observations if isinstance(observations, list) else []:
        if not isinstance(row, dict):
            continue
        if row.get("operation") == "readFile" and row.get("input_scope") == "compiler_library":
            errors.append(prefix + "compiler_library_not_snapshot_bound")
        status = row.get("access_status")
        if status in {"unavailable", "changed_during_probe"}:
            errors.append(prefix + "semantic_host_access_unavailable")
        elif status == "outside_boundary":
            errors.append(prefix + "semantic_host_boundary_denied")
    errors.extend(prefix + issue for issue in semantic_negative_resolution_errors(context, atlas.get(project)))
    errors.extend(prefix + issue for issue in semantic_positive_resolution_errors(context, atlas.get(project)))
    errors.extend(prefix + issue for issue in semantic_auxiliary_input_errors(context, atlas.get(project)))
    errors.extend(prefix + issue for issue in semantic_workspace_read_errors(context, atlas.get(project)))
    errors.extend(prefix + issue for issue in semantic_config_root_read_errors(data, atlas.get(project)))
    errors.extend(prefix + issue for issue in semantic_diagnostic_source_errors(data, atlas.get(project)))
    errors.extend(prefix + issue for issue in semantic_program_source_errors(data, atlas.get(project)))
    errors.extend(prefix + issue for issue in semantic_program_declaration_errors(data, atlas.get(project)))
    errors.extend(prefix + issue for issue in semantic_workspace_external_errors(data, atlas, project))


def checked_source_errors(payload: Any, atlas: dict[str, Any]) -> list[str]:
    if not isinstance(payload, dict) or payload.get("collector_status") != "OK":
        return ["typescript_collector_unavailable"]
    if not isinstance(atlas, dict):
        return ["typescript_atlas_unavailable"]
    projects = payload.get("projects")
    meta = payload.get("meta")
    scope = meta.get("execution_scope") if isinstance(meta, dict) else None
    analyzed = scope.get("analyzed_projects") if isinstance(scope, dict) else None
    requested = scope.get("requested_projects") if isinstance(scope, dict) else None
    if (
        not isinstance(projects, dict) or not projects
        or not isinstance(analyzed, list) or not all(isinstance(p, str) for p in analyzed)
        or set(analyzed) != set(projects) or len(analyzed) != len(projects)
        or not isinstance(requested, list) or not all(isinstance(p, str) for p in requested)
        or {p.upper() for p in requested} != {p.upper() for p in projects}
        or scope.get("unavailable_requested_projects")
    ):
        return ["typescript_project_scope_incomplete"]
    errors: list[str] = []
    for project, data in projects.items():
        prefix = f"typescript:{project}:"
        if not isinstance(data, dict):
            errors.append(prefix + "invalid_project")
            continue
        manifest = data.get("checked_source_manifest")
        if data.get("mode") == "semantic":
            _append_semantic_errors(errors, prefix, data, atlas, project)
            continue  # Complete observations cannot authorize their own snapshot binding.
        if (
            data.get("status") != "OK_SYNTAX_ONLY" or data.get("mode") != "syntax"
            or not isinstance(manifest, dict)
            or manifest.get("version") != "v1"
            or manifest.get("hash_basis") != "sha256_utf8_source_text"
            or manifest.get("scope") != "checked_syntax_files_only"
            or manifest.get("complete") is not True
        ):
            errors.append(prefix + "checked_scope_unavailable")
            continue
        project_atlas = atlas.get(project)
        project_atlas = project_atlas if isinstance(project_atlas, dict) else {}
        project_meta = project_atlas.get("project")
        root = project_meta.get("root") if isinstance(project_meta, dict) else None
        observed_root = data.get("project_root")
        # Snapshot binding compares the recorded lexical root identity. Resolving
        # either path here would consult mutable target filesystem state after
        # Atlas and the collector captured their respective inputs.
        roots = (root, observed_root)
        roots_valid = all(
            isinstance(value, str) and bool(value) and Path(value).is_absolute()
            and not {".", ".."}.intersection(value.replace("\\", "/").split("/"))
            for value in roots
        )
        if not roots_valid:
            errors.append(prefix + "project_root_unavailable")
        root_matches = (
            roots_valid and root.replace("\\", "/") == observed_root.replace("\\", "/")
        )
        if not root_matches:
            errors.append(prefix + "project_root_mismatch")
        files = manifest.get("files")
        summary = data.get("summary")
        summary = summary if isinstance(summary, dict) else {}
        if (
            not isinstance(files, list) or not files
            or summary.get("root_files_checked") != len(files)
            or summary.get("root_files_total") != len(files)
        ):
            errors.append(prefix + "checked_inventory_incomplete")
            continue
        seen: set[str] = set()
        atlas_files = project_atlas.get("files")
        atlas_files = atlas_files if isinstance(atlas_files, dict) else {}
        for row in files:
            if not isinstance(row, dict):
                errors.append(prefix + "invalid_checked_file")
                continue
            file = str(row.get("file") or "")
            parts = PurePosixPath(file).parts
            if (
                not file or "\\" in file or ":" in file
                or file.startswith("/") or ".." in parts
                or str(PurePosixPath(file)) != file or file in seen
            ):
                errors.append(prefix + "unsafe_or_duplicate_checked_file")
                continue
            seen.add(file)
            entry = atlas_files.get(file) or {}
            if (
                not isinstance(entry, dict)
                or not _atlas_source_hash_matches(entry.get("hash"), row)
            ):
                errors.append(prefix + f"source_content_mismatch:{file}")
        diagnostics = data.get("diagnostics")
        if not isinstance(diagnostics, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("file"), str) or row["file"] not in seen
            for row in diagnostics
        ):
            errors.append(prefix + "diagnostic_outside_checked_inventory")
    return sorted(set(errors))

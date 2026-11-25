"""Exact stored TypeScript initializer facts under an explicit target declaration."""
from copy import deepcopy
import hashlib

from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION
from tools.core.atlas_io import load_atlas_file_metadata
from tools.core.config import CODE_MAPS_DIR
from tools.core.external_target_generation import external_target_output_slug, resolve_external_target_artifact_dir
from tools.core.source_coordinates import _utf8_span_validator
from tools.core.source_snapshot_reader import read_source_snapshot_binding
from tools.core.typescript_registry_contract import (
    registry_source_policy, registry_lane_problem, registry_validation_spans,
    registry_member_policy, registry_member_path_valid, registry_member_spans, registry_member_lane_problem,
)


def _target_raw_dir(root):
    target_dir = CODE_MAPS_DIR / 'output/external_targets' / external_target_output_slug(str(root.resolve()))
    artifact_dir, _reason = resolve_external_target_artifact_dir(target_dir)
    return artifact_dir / '.raw'


def compare_typescript_registry(root, raw_source, contract, limits, *, compare):
    """No parser/producer launch, shadow rescue, host Atlas or callable evaluation."""
    source = contract['source']
    raw_dir = _target_raw_dir(root)
    project, path = source['project'], source['atlas_path']
    info = load_atlas_file_metadata(raw_dir, project, path)
    parser = info.get('parser_evidence') if isinstance(info, dict) else None
    if (not isinstance(info, dict) or info.get('project_key') != project or info.get('atlas_rel_path') != path
            or info.get('repo_relative_path') != source['path'] or info.get('language') != 'typescript'
            or info.get('ast_contract_version') != AST_CONTRACT_VERSION
            or not isinstance(info.get('hash'), str) or not info['hash']
            or not isinstance(parser, dict) or parser.get('status') != 'observed'
            or parser.get('parser_kind') != 'typescript_compiler_api' or parser.get('reported_by_adapter') is not True
            or type(parser.get('parser_diagnostic_count')) is not int or parser['parser_diagnostic_count'] != 0):
        raise ValueError('typescript_primary_file_unavailable_or_unbound')
    snapshot = read_source_snapshot_binding(raw_dir, project, path, expected_hash=info.get('hash'))
    text = raw_source.decode('utf-8')
    if snapshot['status'] != 'ok' or snapshot['content'] != text:
        raise ValueError('typescript_snapshot_live_identity_mismatch')
    policy, policy_hash, width = registry_source_policy()
    lane = info.get('literal_registry_evidence')
    spans = list(registry_validation_spans(lane, policy['limits']))
    member_path = source.get('member_path')
    member_lane = info.get('literal_registry_member_evidence')
    member_policy = None
    if member_path is not None:
        member_policy, member_digest, member_width = registry_member_policy()
        if member_digest != policy_hash or member_width != width or not registry_member_path_valid(member_path,member_policy,width):
            raise ValueError('typescript_member_policy_or_path_changed')
        spans.extend(registry_member_spans(member_lane,member_policy['limits'],validation=True))
    valid = _utf8_span_validator(text, spans)
    problem = registry_lane_problem(lane, valid, identity=hashlib.sha256(text.encode('utf-8')).hexdigest(),
        policy=policy, policy_hash=policy_hash, name_limit=width, ast_version=AST_CONTRACT_VERSION)
    if problem:
        raise ValueError(problem)
    tables = [row for row in lane['records'] if row['name'] == source['symbol']]
    if len(tables) != 1 or tables[0]['status'] != 'observed':
        raise ValueError('typescript_selected_table_not_fully_observed')
    selected_rows = tables[0]['rows']
    if member_policy is not None:
        problem = registry_member_lane_problem(member_lane,lane,valid,identity=hashlib.sha256(text.encode('utf-8')).hexdigest(),
            policy=member_policy,root_policy=policy,policy_hash=policy_hash,name_limit=width,ast_version=AST_CONTRACT_VERSION)
        if problem:
            raise ValueError(problem)
        index = {(r['root_name'],tuple(r['member_path'])):r for r in member_lane['records']}
        chosen = (next((r for r in tables[0]['rows'] if r['key']==member_path[0]),None) if len(member_path)==1
                  else index.get((source['symbol'],tuple(member_path))))
        if chosen is None or chosen.get('status','observed') != 'observed':
            raise ValueError('typescript_selected_member_path_not_fully_observed')
        selected_rows = []
        for field in chosen['fields']:
            child = index.get((source['symbol'],tuple([*member_path,field['key']])))
            if child is None or child['status'] != 'observed' or field['spans']['value'] != child['object_span']:
                raise ValueError('typescript_selected_member_table_requires_direct_object_rows')
            selected_rows.append({'key':field['key'],'fields':child['fields'],
                'spans':{'key':field['spans']['key'],'object':child['object_span']}})
    values, locations = {}, {}
    for row in selected_rows:
        values[row['key']] = {f['key']: f['value'] if f['kind'] == 'string_literal' else None for f in row['fields']}
        field = next((f for f in row['fields'] if f['key'] == contract['id_field']), None)
        span = field['spans']['value'] if field else row['spans']['object']
        locations[row['key']] = {'key_span': deepcopy(row['spans']['key']), 'source_span': deepcopy(span),
            'source_followup': {'tool':'inspect_file', 'file_path':f"{project}::{source['path']}",
                                'line_start':span['line'], 'line_end':span['end_line']}}
    result = compare(values, contract, limits, locations=locations)
    if (load_atlas_file_metadata(raw_dir, project, path) != info
            or read_source_snapshot_binding(raw_dir, project, path, expected_hash=info.get('hash')) != snapshot):
        raise ValueError('typescript_primary_or_snapshot_changed_during_review')
    path_details = {'source_member_path':list(member_path),'path_semantics':'literal_property_selection_not_evaluation'} if member_path is not None else {}
    return {**result, **path_details,
        'coverage':'all_rows_of_explicit_literal_member_path_only' if member_path is not None else 'all_rows_of_selected_literal_initializer_only', 'source_symbol':source['symbol'],
        'atlas_binding':'verified_selected_file_and_snapshot', 'ast_contract_version':AST_CONTRACT_VERSION,
        'atlas_file_hash':info['hash'], 'initializer_value_semantics':'not_runtime_state', 'offset_unit':'utf8_bytes'}

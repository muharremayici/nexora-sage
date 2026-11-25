"""Private declared-metadata preflight. No capture, subprocess or launch API."""
from dataclasses import dataclass,field
import json
import re
from tools.core.owned_text_bridge import canonical
from tools.core.atlas_integrity import payload_sha256
from tools.core.artifact_validator import _validate

PROFILE='private_source_config_parent_preparation'
_TOKEN=object()


@dataclass(frozen=True,eq=False)
class PreparationContext:
    metadata: str=field(repr=False)
    token: object=field(repr=False)


def _digest(value):
    return isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value) is not None


def hold_preparation_context(*,expected,policies,schemas):
    """Hold independent owner references, not child JSON or execution authority."""
    policy=policies['lineage']['artifacts']['ts_diagnostics'][PROFILE]
    errors=[];schema=schemas['preparation']
    _validate(policy,schema,schema,['preparation_policy'],errors)
    if errors or policy['version']!='v1-private-source-config-preparation' or any(policy[k] is not False
            for k in ('default_enabled','production_enabled','semantic_lineage','cache_eligible','native_execution_enabled')):
        raise ValueError('join_preparation_policy_refused')
    keys={'invocation_id','attempt_id','config_invocation_id','compiler_source_sha256','capture_sha256','source_sha256',
        'parser_response_sha256','auxiliary_context_sha256','parent_snapshot_id','atlas_sha256',
        'parent_validation_sha256','config_binding_sha256'}
    settings=policies['lineage']['artifacts']['ts_diagnostics']
    config=settings[settings[policy['config_profile']]['config_limit_profile']]
    limit=settings[config['bridge_profile']]['max_request_id_chars']
    if (not isinstance(expected,dict) or set(expected)!=keys
            or any(not _digest(value) for key,value in expected.items() if key.endswith('_sha256') or key=='parent_snapshot_id')
            or any(not isinstance(expected[k],str) or not re.fullmatch('[A-Za-z0-9_-]+',expected[k]) or len(expected[k])>limit
                for k in ('invocation_id','attempt_id','config_invocation_id'))):
        raise ValueError('join_preparation_owner_refused')
    return PreparationContext(canonical({'expected':expected,'policies':policies,'schemas':schemas}),_TOKEN)


def _require(condition,code):
    if not condition:raise ValueError(code)


def _source_counters(receipt,expected,policy):
    counters=receipt['counters'];transport=receipt['transport']
    _require(counters['version']=='v1-outer-compiler-api-cost' and counters['basis']=='outer_api_calls_only'
        and counters['native_execution_attested'] is False and counters['disposed'] is True
        and counters['context']=={k:expected[k] for k in ('invocation_id','attempt_id','compiler_source_sha256')}
        and receipt['loader_restored'] is True,'join_source_counter_context_refused')
    _require(set(transport)=={'worker_process_starts','worker_restarts','request_replays'}
        and all(type(v) is int for v in transport.values())
        and transport=={'worker_process_starts':1,'worker_restarts':0,'request_replays':0},'join_attempt_cost_unknown')
    _require(set(counters['counts'])==set(policy['counter_limits']),'join_source_counter_incomplete')
    for name,limit in policy['counter_limits'].items():
        row=counters['counts'][name]
        _require(set(row)=={'attempted','forwarded','completed','thrown','denied'}
            and all(type(v) is int and v>=0 for v in row.values())
            and row['attempted']==row['forwarded']==row['completed']<=limit
            and row['thrown']==row['denied']==0,'join_source_counter_incomplete')
    _require(counters['counts']['createSourceFile']['forwarded']==1,'join_source_counter_incomplete')


def check_join_preparation(stages,*,expected_context):
    """Check declared order/correspondence only; never launches or attests work.

    The caller owns canonical parent/schema/policy and receipt origin. This
    preflight checks its separately held references, not that a validator or
    a native process actually executed. Modeled receipts stay modeled.
    """
    out={'version':'v1-private-source-config-preparation','status':'UNKNOWN_JOIN_PREPARATION_CONTEXT',
        'basis':'declared_metadata_only','code':'join_preparation_owner_unavailable','context_sha256':None,
        'accepted_phases':[],'next_phase':None,'native_execution_enabled':False,'production_enabled':False,
        'semantic_lineage':False,'cache_eligible':False,'execution_attested':False}
    if not isinstance(expected_context,PreparationContext) or expected_context.token is not _TOKEN:return out
    held=json.loads(expected_context.metadata);expected=held['expected']
    settings=held['policies']['lineage']['artifacts']['ts_diagnostics'];policy=settings[PROFILE]
    config=settings[settings[policy['config_profile']]['config_limit_profile']]
    order=policy['phase_order'];out.update(context_sha256=payload_sha256(held),code='')
    try:
        _require(isinstance(stages,list) and len(stages)<=len(order),'join_phase_order_refused')
        _require(len(canonical(stages).encode('utf-8'))<=config['max_request_bytes'],'join_preparation_byte_budget_exceeded')
        for index,stage in enumerate(stages):
            _require(isinstance(stage,dict) and set(stage)=={'phase','invocation_id','receipt'}
                and stage['phase']==order[index] and stage['invocation_id']==expected['invocation_id'],
                'join_phase_order_refused')
            row=stage['receipt'];_require(isinstance(row,dict),'join_stage_metadata_refused')
            phase=stage['phase']
            if phase=='captured_inputs_held':
                _require(set(row)=={'capture_sha256','source_sha256','auxiliary_context_sha256','content_reads','canonical_inputs'}
                    and all(row[k]==expected[k] for k in ('capture_sha256','source_sha256','auxiliary_context_sha256'))
                    and row['canonical_inputs'] is True and row['content_reads']=={'source':1,'root':1,'base':1}
                    and all(type(v) is int for v in row['content_reads'].values()),'join_capture_reference_refused')
            elif phase=='source_parser_observed':
                _require(set(row)=={'source_sha256','parser_response_sha256','parser_status','counters','transport','loader_restored'}
                    and row['parser_status']=='observed' and all(row[k]==expected[k] for k in ('source_sha256','parser_response_sha256')),
                    'join_source_parser_reference_refused')
                _source_counters(row,expected,policy)
            elif phase=='atlas_parent_validated':
                _require(set(row)=={'atlas','commit','validation_receipt_sha256','parser_response_sha256','hash_override_used'}
                    and row['validation_receipt_sha256']==expected['parent_validation_sha256']
                    and row['parser_response_sha256']==expected['parser_response_sha256']
                    and row['hash_override_used'] is False,'join_parent_reference_refused')
                errors=[];schema=held['schemas']['atlas'];_validate(row['atlas'],schema,schema,['parent'],errors)
                _require(not errors,'join_parent_schema_refused')
                _require(row['commit']['state']=='complete' and row['commit']['snapshot_id']==expected['parent_snapshot_id']
                    and row['commit']['atlas_sha256']==expected['atlas_sha256']==payload_sha256(row['atlas']),
                    'join_parent_reference_refused')
                source=row['atlas'][policy['project_key']]['files'][settings[policy['config_profile']]['declared_source_name']]
                _require(source['hash']==expected['source_sha256'] and source['canonical_file_path'] is True
                    and source['parser_evidence']['status']=='observed','join_parent_source_refused')
            elif phase=='parent_frozen':
                _require(set(row)=={'parent_snapshot_id','atlas_sha256','frozen'} and row['frozen'] is True
                    and all(row[k]==expected[k] for k in ('parent_snapshot_id','atlas_sha256')),'join_parent_freeze_refused')
            elif phase=='config_chain_observed':
                _require(set(row)=={'parent_snapshot_id','atlas_sha256','config_binding_sha256','config_invocation_id','capture_sha256',
                    'config_chain_calls','config_sourcefiles','config_programs','collector_executed'}
                    and all(row[k]==expected[k] for k in ('parent_snapshot_id','atlas_sha256','config_binding_sha256','config_invocation_id','capture_sha256'))
                    and all(type(row[k]) is int for k in ('config_chain_calls','config_sourcefiles','config_programs'))
                    and [row[k] for k in ('config_chain_calls','config_sourcefiles','config_programs')]==[1,2,0]
                    and row['collector_executed'] is False,'join_config_reference_refused')
            else:raise ValueError('join_phase_order_refused')
            out['accepted_phases'].append(phase)
        count=len(out['accepted_phases'])
        out.update(status='JOIN_PREPARATION_METADATA_CONFORMANT_NOT_EXECUTED' if count==len(order) else 'INCOMPLETE_JOIN_PREPARATION_METADATA',
            code='' if count==len(order) else 'join_stage_receipts_incomplete',next_phase=None if count==len(order) else order[count])
    except (ValueError,TypeError,KeyError,AttributeError,RecursionError) as error:
        code=str(error);allowed=policy['refusal_codes']
        out.update(status='REFUSED_JOIN_PREPARATION_METADATA',code=code if code in allowed else 'join_stage_metadata_refused',next_phase=None)
    return out

"""Private synthetic image-process metadata admission; no capture or compiler IO."""
import base64
import hashlib

from tools.core.artifact_validator import _validate
from tools.core.typescript_source_binding import _semantic_metadata_sha as sha, _semantic_metadata_json as canonical, _compiler_stream_stager_manifest

PROFILE='private_prepared_compiler_image_process'


def _need(condition):
    if not condition:
        raise ValueError('synthetic image process context')


def prepare_context(held, errors):
    """Validate copied caller context and oracle; return only bounded metadata refs."""
    profiles, wire_schema, expected = (held[k] for k in ('profiles','schema','expected'))
    ctx=held['image_context'];schema=ctx['composition_schema'];p=profiles[PROFILE]
    for value, definition in [(p,'driver_policy'),(ctx,p['context_definition']),(held['expected_completion'],p['expected_completion_definition'])]:
        _validate(value,schema['$defs'][definition],schema,[definition],errors)
    _need(not errors)
    _need(p['version']=='v1-private-synthetic-prepared-base-image-process' and p['model']=='synthetic_prepared_base_image_one_credit_v1')
    _need(all(p[k] is False for k in ['default_enabled','native_execution_enabled','raw_transport_enabled','production_enabled','semantic_lineage','cache_eligible']))
    _need(p['schema_sha256']==sha(schema))
    image=profiles[p['image_profile']];source=profiles[p['source_profile']];bridge=profiles[p['bridge_profile']]
    prep=profiles[p['preparation_profile']];ingress=profiles[p['ingress_profile']];layout=profiles[p['layout_profile']]
    for name,value in [('image',image),('source',source),('bridge',bridge),('preparation',prep),('ingress',ingress)]:
        _need(sha(value)==p[name+'_policy_sha256'])
    _need(prep['schema_sha256']==sha(ctx['preparation_schema']) and ingress['schema_sha256']==sha(ctx['admission']['schema']))
    admission=ctx['admission'];binding=ctx['binding'];outer=ctx['source_context']['binding'];rows=ctx['entries'];limit=image['limits']
    _need(set(admission)=={'policy','schema','expected','compiler_manifest'} and admission['policy']==ingress)
    _validate(admission['expected'],admission['schema']['$defs']['expected'],admission['schema'],['expected'],errors)
    _validate(expected,wire_schema['$defs']['stager_expected'],wire_schema,['expected'],errors)
    _need(not errors)
    manifest=admission['compiler_manifest'];summary=_compiler_stream_stager_manifest(manifest,held)
    _need(binding['policy_sha256']==sha(image) and binding['compiler_version']==expected['compiler_version'] and binding['compiler_source_sha256']==expected['compiler_source_sha256'])
    _need(ctx['source_context']['expected_sha256']==sha(outer))
    for key in ['atlas_snapshot_id','atlas_sha256','compiler_version','compiler_source_sha256']:
        _need(outer[key]==binding[key])
    _need(outer['invocation_id']==binding['producer_run_id'] and 0<len(outer['invocation_id'])<=bridge['max_request_id_chars'])
    for key,value in [('bridge_policy_sha256',sha(bridge)),('handoff_policy_sha256',sha(source)),('image_policy_sha256',sha(image)),
        ('parser_virtual_path',bridge['virtual_file']),('compiler_virtual_path',source['compiler_virtual_path']),('atlas_relative_path',source['atlas_relative_path']),
        ('auxiliary_virtual_path',source['auxiliary_virtual_path']),('auxiliary_atlas_path',source['auxiliary_atlas_path']),
        ('config_virtual_path',source['config_virtual_path']),('config_atlas_path',source['config_atlas_path'])]:
        _need(outer[key]==value)
    _need(outer['config_context_sha256']==outer['auxiliary_context_sha256'])
    _need(binding['source_sha256']=={source['compiler_virtual_path']:outer['source_sha256']})
    _need([row['kind'] for row in rows]==p['input_kinds'])
    records=[];raw_total=0;aux_total=0
    for row in rows:
        kind=row['kind'];primary=kind=='source';prefix='source' if primary else kind
        path=source['compiler_virtual_path' if primary else kind+'_virtual_path']
        cap=min(bridge['max_source_bytes'],limit['source_file_bytes']) if primary else min(source['max_config_bytes'] if kind=='config' else source['max_auxiliary_bytes'],limit['auxiliary_file_bytes'])
        _need(row['path']==path and row['role']==('source' if primary else 'auxiliary') and type(row['bytes']) is int and 0<=row['bytes']<=cap)
        _need(row['bytes']==outer[prefix+'_bytes'] and row['raw_sha256']==outer['source_sha256' if primary else prefix+'_raw_sha256'])
        if not primary:_need(row['host_sha256']==outer[prefix+'_host_sha256'])
        raw=base64.b64decode(row['utf8Base64'],validate=True)
        _need(len(raw)==row['bytes'] and base64.b64encode(raw).decode('ascii')==row['utf8Base64'] and hashlib.sha256(raw).hexdigest()==row['raw_sha256'])
        text=raw.decode('utf-8');host=text.removeprefix('\ufeff')
        _need(hashlib.sha256(host.encode()).hexdigest()==row['host_sha256'])
        records.append({'path':path,'role':row['role'],'size_bytes':row['bytes'],'raw_sha256':row['raw_sha256'],'parser_sha256':row['raw_sha256'],'host_sha256':row['host_sha256']})
        raw_total+=row['bytes'];aux_total+=0 if primary else row['bytes']
    _need(aux_total<=min(source['max_auxiliary_bytes'],limit['auxiliary_bytes']) and raw_total-aux_total<=limit['source_bytes'])
    records.sort(key=lambda row:row['path']);ae=admission['expected']
    refs={k:expected[k] for k in ['context_sha256','inventory_sha256','manifest_sha256']}
    refs.update(preparation_policy_sha256=sha(prep),preparation_schema_sha256=sha(ctx['preparation_schema']),image_policy_sha256=sha(image))
    _need(ae=={'binding_sha256':sha(binding),'source_inputs_sha256':sha(records),'ingress_policy_sha256':sha(ingress),
        'ingress_schema_sha256':sha(admission['schema']),'stream_session_id':expected['session_id'],'preparation':refs})
    _need(manifest['mapping_root']==layout['mapping_root'])
    completion=held['expected_completion'];image_summary=completion['image_summary']
    _validate(image_summary,admission['schema']['$defs']['summary'],admission['schema'],['image_summary'],errors)
    _need(not errors)
    _need(type(completion['files_completed']) is int and completion['files_completed']==summary['library_files'])
    _need(type(completion['raw_bytes_staged']) is int and completion['raw_bytes_staged']==summary['library_raw_bytes'])
    _need(all(type(image_summary[k]) is int for k in ['file_count','content_bytes','metadata_bytes','ingress_metadata_bytes','admission_stage_known_metadata_bytes','admission_stage_known_image_bytes']))
    stage=len(canonical({'binding':binding,'image_policy':image,'admission':admission}).encode('utf-8'))
    _need(image_summary['file_count']==len(rows)+summary['library_files'] and image_summary['content_bytes']==raw_total+summary['library_raw_bytes'])
    _need(image_summary['ingress_metadata_bytes']==stage and image_summary['admission_stage_known_metadata_bytes']==image_summary['metadata_bytes']+stage)
    _need(image_summary['admission_stage_known_image_bytes']==image_summary['content_bytes']+image_summary['admission_stage_known_metadata_bytes'])
    _need(image_summary['admission_stage_known_metadata_bytes']<=limit['metadata_bytes'] and image_summary['admission_stage_known_image_bytes']<=limit['image_bytes'])
    _need(image_summary['envelope_sha256']==sha({'imageIdentity':image_summary['image_sha256'],'binding':binding}))
    _need(image_summary['prepared_ingress_sha256']==sha({'expected':ae,'manifest_sha256':refs['manifest_sha256'],'policy_sha256':ae['ingress_policy_sha256'],'schema_sha256':ae['ingress_schema_sha256']}))
    return process_refs(held)


def process_refs(held):
    """Metadata identity only; never manufacture the independent final-image oracle."""
    p=held['profiles'][PROFILE];ctx=held['image_context'];ae=ctx['admission']['expected']
    refs={'composition_policy_sha256':sha(p),'composition_schema_sha256':sha(ctx['composition_schema']),
          'source_context_sha256':ctx['source_context']['expected_sha256']}
    refs['process_context_sha256']=sha({'version':p['version'],**refs,'stream_expected':held['expected'],
        'layout_sha256':sha(held['profiles'][p['layout_profile']]),'preparation_schema_sha256':sha(ctx['preparation_schema']),
        'ingress_expected':ae,'binding_sha256':sha(ctx['binding'])})
    return refs


def project_bootstrap(held, remaining_ms):
    """Closed wire projection: final-image completion remains parent-only."""
    p=held['profiles'][PROFILE];profiles=held['profiles'];layout=profiles[p['layout_profile']]
    js=profiles['private_compiler_stream_js_stager']
    names={PROFILE,p['layout_profile'],p['image_profile'],p['source_profile'],p['bridge_profile'],p['preparation_profile'],p['ingress_profile'],
           layout['wire_profile'],layout['capture_profile'],'private_compiler_stream_js_stager',js['stager_profile']}
    _need(type(remaining_ms) is int and remaining_ms>0)
    return {'kind':'bootstrap','version':p['version'],'expected':held['expected'],'profiles':{key:profiles[key] for key in sorted(names)},
        'schema':held['schema'],'image_context':held['image_context'],'process_refs':process_refs(held),'remaining_ms':remaining_ms}

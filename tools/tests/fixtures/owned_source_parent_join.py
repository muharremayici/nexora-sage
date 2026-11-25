"""Private single synthetic join. No CLI, production entry point or collector."""
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

from tools.core.artifact_validator import _validate, validate_against_schema
from tools.core import typescript_source_binding as source_binding
from tools.core.atlas_integrity import build_atlas_commit, validate_atlas_commit, payload_sha256
from tools.core.json_io import load_json_object_strict
from tools.core.node_ast_worker import NodeAstWorkerSession
from tools.core.owned_config_input import capture_owned_extends_inputs
from tools.core.owned_text_bridge import (canonical, policy_identity, config_input_policy_identity,
    create_owned_wire_base, observe_owned_wire_config, owned_wire_base_snapshot, dispose_owned_wire_base)
from tools.core.python_runtime_env import utf8_subprocess_env
from tools.core.repository_topology import project_owned_path_identity
from tools.engines.generate_atlas import (_load_atlas_source_entry, _normalize_polyglot_symbols,
    _file_parser_evidence, _extract_exports_for_language, _build_project_symbol_occurrences)
from tools.tests.fixtures.owned_source_parent_preparation import hold_preparation_context, check_join_preparation

ROOT = Path(__file__).resolve().parents[3]
sha = lambda raw: hashlib.sha256(raw).hexdigest()
digest = lambda obj: sha(canonical(obj).encode('utf-8'))


class JoinRefused(ValueError):
    """Safe operation code and bounded progress only, never raw input/errors."""
    def __init__(self, code, observation):
        super().__init__(code)
        self.observation = deepcopy(observation)


def require(condition, code):
    if not condition:
        raise ValueError(code)


@dataclass(frozen=True)
class ValidatedParent:
    atlas_json: str = field(repr=False)
    commit_json: str = field(repr=False)
    validation_sha256: str


def freeze_parent(atlas):
    """Validate actual schema/payload with canonical owners; no hash override."""
    require(not validate_against_schema(ROOT/'config/schemas/atlas.schema.json', 'private_parent', atlas),
        'join_parent_schema')
    commit = build_atlas_commit(atlas)
    checks = validate_atlas_commit(atlas, commit)
    require(bool(checks) and all(row['passed'] for row in checks), 'join_parent_validation')
    require(payload_sha256(atlas) == commit['atlas_sha256'], 'join_parent_payload')
    return ValidatedParent(canonical(atlas), canonical(commit), digest(checks))


def freeze_source_parent(atlas):
    """Project the finite file set through the ordinary index owner before validation."""
    atlas['MAIN']['symbols'] = _build_project_symbol_occurrences(atlas['MAIN']['files'])
    return freeze_parent(atlas)


def config_request(captured, parent, profiles, compiler, invocation):
    policy = profiles['private_owned_config_extends']; limits = profiles[policy['config_limit_profile']]
    commit = json.loads(parent.commit_json); wire = captured.wire_sources()
    binding = {'atlas_snapshot_id': commit['snapshot_id'], 'atlas_sha256': commit['atlas_sha256'],
        'invocation_id': invocation, 'compiler_version': compiler['version'],
        'compiler_source_sha256': compiler['source_sha256'], 'policy_sha256': digest(policy),
        'auxiliary_policy_sha256': digest(profiles['atlas_auxiliary_inputs']),
        'context_sha256': captured.context_sha256,
        'input_policy_sha256': config_input_policy_identity(limits, profiles[limits['image_profile']],
            profiles[limits['bridge_profile']], profiles['atlas_auxiliary_inputs']),
        'files': {role: {**row, 'bytes': wire[row['atlas_path']]['bytes'],
            'raw_sha256': wire[row['atlas_path']]['raw_sha256'],
            'host_sha256': wire[row['atlas_path']]['host_text_sha256']} for role,row in policy['roster'].items()}}
    return {'version': policy['version'], 'binding': deepcopy(binding),
        'files': {role: wire[row['atlas_path']] for role,row in policy['roster'].items()}}, binding


def source_receipt(response, transport, expected_context):
    """Finite independent response-cost check; a missing lost-attempt cost never passes."""
    cost = response['batchMeta']['privateSourceCost']
    require(set(cost) == {'parser_payload_sha256','counters','loader_restored','writer_restored'}, 'join_cost_shape')
    # JS stringify preserves result insertion order; the source worker's unmodified
    # result object is hashed separately from the additive private cost metadata.
    result_text = json.dumps(response['results'], ensure_ascii=False, separators=(',', ':'))
    require(cost['parser_payload_sha256'] == sha(result_text.encode('utf-8')), 'join_parser_payload')
    require(cost['loader_restored'] is True and cost['writer_restored'] is True, 'join_restore')
    require(transport == {'worker_process_starts':1,'worker_restarts':0,'request_replays':0} and
        all(type(value) is int for value in transport.values()), 'join_attempt_cost_unknown')
    counters = cost['counters']
    require(counters['context'] == expected_context and counters['disposed'] is True and
        counters['basis'] == 'outer_api_calls_only' and counters['native_execution_attested'] is False,
        'join_counter_context')
    require(set(counters['counts']) == {'createSourceFile','createProgram'}, 'join_counter_shape')
    for name, row in counters['counts'].items():
        require(set(row) == {'attempted','forwarded','completed','thrown','denied'} and
            all(type(n) is int and n >= 0 for n in row.values()), 'join_counter_shape')
        n = row['forwarded']
        require(n in (0,1) and row['attempted'] == row['completed'] == n and
            row['thrown'] == row['denied'] == 0, 'join_counter_cost_unknown')
    require(counters['counts']['createSourceFile']['completed'] == 1, 'join_source_parse_missing')
    return deepcopy(cost)


def load_join_live_context_inputs(lineage):
    """Optional policy/schema reads only; unavailable metadata never relabels base work."""
    try:
        profiles = lineage['artifacts']['ts_diagnostics']
        require(profiles['private_live_config_context']['schema'] == 'config/schemas/typescript_live_config_context.schema.json'
            and profiles['private_owned_config_extends']['output_schema'] == 'config/schemas/owned_config_extends_observation.schema.json',
            'join_live_schema_path_refused')
        return {'policy_inputs': {'lineage': lineage,
            'algorithms': load_json_object_strict(ROOT/'config/source_snapshot_store_policy.json')['content_identity']['digest_algorithms']},
            'schemas': {'view': load_json_object_strict(ROOT/profiles['private_live_config_context']['schema']),
                'config': load_json_object_strict(ROOT/profiles['private_owned_config_extends']['output_schema']),
                'commit': load_json_object_strict(ROOT/'config/schemas/atlas_commit.schema.json')}}
    except Exception:
        # The explicit attachment below will report UNAVAILABLE; no exception text.
        return None


def project_join_live_context(*, atlas, parent, source_sha256, binding, observation, inputs):
    """Pure optional attachment using actual upstream locals, never a native replay.

    The outer caller owns actual canonical parent/config validation. A view-only
    failure is explicit UNAVAILABLE and cannot change or promote that base result.
    Full Atlas, commit, binding and arbitrary errors never enter the attachment.
    """
    unavailable = {'state': 'UNAVAILABLE', 'result': None}
    if inputs is None:
        return unavailable
    try:
        require(canonical(atlas) == parent.atlas_json, 'join_live_parent_changed')
        context = source_binding.hold_live_config_context(atlas_commit=json.loads(parent.commit_json),
            parent_validation_sha256=parent.validation_sha256, project='MAIN', source_sha256=source_sha256,
            config_binding=binding, observation_sha256=digest(observation), **inputs)
        view = source_binding.project_live_config_context(atlas, observation, expected_context=context)
        attachment = {'state': 'RESULT_RETURNED', 'result': view}
        schema = inputs['schemas']['view']; errors = []
        _validate(attachment, schema['$defs']['join_attachment'], schema, ['join_live_context'], errors)
        settings = inputs['policy_inputs']['lineage']['artifacts']['ts_diagnostics']
        config = settings[settings['private_owned_config_extends']['config_limit_profile']]
        limit = settings[config['image_profile']]['limits']['metadata_bytes']
        require(not errors and len(canonical(attachment).encode('utf-8')) <= limit, 'join_live_context_result_unavailable')
        return attachment
    except Exception:
        return unavailable


def prepare_join_wire_base(profiles, source, captured, compiler, invocation, config_invocation):
    """Private opt-in metadata preparation; never call either raw wire builder."""
    try:
        descriptor = profiles['private_owned_wire_base']
        require(descriptor['schema'] == 'config/schemas/owned_wire_base.schema.json', 'join_wire_base_schema_path')
        schema = load_json_object_strict(ROOT / descriptor['schema'])
        errors = []
        _validate(descriptor, schema, schema, ['wire_base_profile'], errors)
        require(not errors and descriptor['schema_sha256'] == digest(schema), 'join_wire_base_schema')
        config = profiles['private_owned_config_extends']
        inventory = json.loads(captured.inventory_json)
        files = {}
        for role, owner in config['roster'].items():
            row = inventory['files'][owner['atlas_path']]
            require(row['size_bytes'] == len(captured.raw_by_name[owner['atlas_path']]), 'join_wire_base_capture')
            files[role] = {**owner, 'bytes': row['size_bytes'], 'raw_sha256': row['raw_sha256'],
                'host_sha256': row['host_text_sha256']}
        expected = {'source': {'request_id': invocation, 'source_bytes': len(source.raw),
            'source_sha256': source.source_sha256, 'source_policy_sha256': policy_identity(json.loads(source.policy_json))},
            'config': {'invocation_id': config_invocation, 'context_sha256': captured.context_sha256,
                'compiler_version': compiler['version'], 'compiler_source_sha256': compiler['source_sha256'], 'files': files}}
        receiver = create_owned_wire_base(profiles, expected)
        return receiver, schema
    except Exception:
        return None, None


def project_join_wire_base(receiver, schema):
    """A returned construction receipt may still be incomplete, never admission."""
    try:
        result = owned_wire_base_snapshot(receiver)
        errors = []
        _validate(result, schema['$defs']['receipt'], schema, ['wire_base_receipt'], errors)
        require(not errors, 'join_wire_base_result')
        return {'state': 'RESULT_RETURNED', 'result': result}
    except Exception:
        return {'state': 'UNAVAILABLE', 'result': None}


def pin_join_compiler_plan_inputs(inputs):
    """Borrow only the opaque owner; pin separate JSON metadata, without IO.

    The existing invocation policy supplies the metadata ceiling after base work.
    Serialization is not an OS memory bound. Resource errors/cancellation escape.
    """
    try:
        require(type(inputs) is dict and {'capsule', 'expected_inventory'} <= set(inputs)
            and set(inputs) <= {'capsule', 'expected_inventory', 'include_package'}, 'join_compiler_plan_inputs')
        include_package = inputs.get('include_package', False)
        require(type(include_package) is bool and type(inputs['expected_inventory']) is dict, 'join_compiler_plan_inputs')
        metadata = canonical({'expected_inventory': inputs['expected_inventory'], 'include_package': include_package})
        return {'capsule': inputs['capsule'], 'metadata_json': metadata}
    except (KeyError, TypeError, ValueError, UnicodeError):
        return None


def project_join_compiler_plan(*, pinned, include_wire_base, receiver, receipt, wire_schema,
                               profiles, compiler, expected_wire):
    """Optional metadata-only attachment. Never capture, close, replay or admit.

    Caller-validated upstream references remain distinct from receipt claims.
    Known optional refusals preserve the base result; unexpected/resource/cancel
    errors propagate outside the legacy base catch, through receiver cleanup.
    """
    code = 'join_compiler_plan_inputs_unavailable'
    try:
        require(pinned is not None, code)
        code = 'join_compiler_plan_measurement_required'
        require(include_wire_base is True, code)
        code = 'join_compiler_plan_receipt_unavailable'
        require(type(receipt) is dict and receipt.get('status') == 'OWNED_WIRE_BASE_DERIVED_NOT_ADMITTED'
            and wire_schema is not None, code)
        code = 'join_compiler_plan_metadata_unavailable'
        descriptor = profiles['private_compiler_transport_plan']
        wire_descriptor = profiles['private_owned_wire_base']
        ceiling = profiles[descriptor['image_profile']]['limits']['metadata_bytes']
        require(type(ceiling) is int and ceiling > 0 and len(pinned['metadata_json'].encode('utf-8')) <= ceiling, code)
        require(descriptor['schema'] == 'config/schemas/compiler_library_transport_plan.schema.json'
            and wire_descriptor['schema'] == 'config/schemas/owned_wire_base.schema.json'
            and wire_descriptor['schema_sha256'] == digest(wire_schema), code)
        # One named schema read; reuse the invocation policy and existing wire schema.
        plan_schema = load_json_object_strict(ROOT / descriptor['schema'])
        errors = []
        _validate(descriptor, plan_schema, plan_schema, ['compiler_plan_policy'], errors)
        _validate(receipt, wire_schema['$defs']['receipt'], wire_schema, ['wire_receipt'], errors)
        require(not errors and descriptor['schema_sha256'] == digest(plan_schema), code)
        held = json.loads(pinned['metadata_json'])
        code = 'join_compiler_plan_identity_mismatch'
        inventory = held['expected_inventory']
        require(compiler['version'] == inventory['compiler_version']
            and compiler['source_sha256'] == inventory['installed_module_sha256'], code)
        references = {**expected_wire, 'receipt_sha256': digest(receipt)}
        code = 'join_compiler_plan_adapter_unavailable'
        result = source_binding._plan_owned_compiler_transport_from_wire_base(pinned['capsule'],
            expected_inventory=inventory, receiver=receiver, receipt=receipt, expected_wire=references,
            profiles=profiles, schemas={'plan': plan_schema, 'wire': wire_schema}, include_package=held['include_package'])
        code = 'join_compiler_plan_result_unavailable'
        _validate(result, plan_schema['$defs']['wire_adapter'], plan_schema, ['compiler_plan_result'], errors)
        require(not errors, code)
        code = 'join_compiler_plan_adapter_unavailable'
        require(result['status'] == 'WIRE_BASE_COMPILER_PLAN_RETURNED_NOT_ADMITTED', code)
        attachment = {'state': 'RESULT_RETURNED', 'code': '', 'result': result}
        code = 'join_compiler_plan_result_unavailable'
        _validate(attachment, plan_schema['$defs']['join_attachment'], plan_schema, ['compiler_plan_attachment'], errors)
        require(not errors and len(canonical(attachment).encode('utf-8')) <= ceiling, code)
        return attachment
    except (KeyError, TypeError, ValueError, UnicodeError, OSError):
        return {'state': 'UNAVAILABLE', 'code': code, 'result': None}


def run_join(directory, *, include_live_context=False, include_wire_base=False, compiler_plan_inputs=None):
    """One caller-owned execution; do not call again to turn a failed attempt into PASS."""
    observed = {'status':'INCOMPLETE_PRIVATE_SOURCE_CONFIG_JOIN','phase':'preflight',
        'source_worker_requests':0,'config_child_launches':0,'collector_runs':0,
        'production_enabled':False,'semantic_lineage':False,'cache_eligible':False}
    wire_base = wire_schema = None
    try:
        pinned_compiler = pin_join_compiler_plan_inputs(compiler_plan_inputs) if compiler_plan_inputs is not None else None
        try:
            if include_wire_base is not False:
                require(type(include_wire_base) is bool, 'join_wire_base_flag_refused')
            require(type(include_live_context) is bool, 'join_live_context_flag_refused')
            lineage = load_json_object_strict(ROOT/'config/analysis_snapshot_lineage_contract.json')
            live_inputs = load_join_live_context_inputs(lineage) if include_live_context else None
            profiles = lineage['artifacts']['ts_diagnostics']; policy = profiles['private_owned_config_extends']
            preparation_schema = load_json_object_strict(ROOT/'config/schemas/owned_source_parent_preparation.schema.json')
            atlas_schema = load_json_object_strict(ROOT/'config/schemas/atlas.schema.json')
            provenance = load_json_object_strict(ROOT/'config/third_party_distribution_contract.json')
            owner = next(row for row in provenance['vendored_dependencies'] if row['id']=='typescript')
            package_path = ROOT/owner['runtime_package_path']; manifest = load_json_object_strict(package_path)
            module = (package_path.parent/manifest['main']).resolve(strict=True)
            require(module.is_relative_to(package_path.parent.resolve()) and
                manifest['version'] == owner['locked_version'] == policy['compiler_version'], 'join_compiler_identity')
            compiler = {'version':manifest['version'],'source_sha256':sha(module.read_bytes())}
            invocation, attempt, config_invocation = 'private-source-parent-join', 'single-attempt', 'private-parent-config'
            context = {'invocation_id':invocation,'attempt_id':attempt,'compiler_source_sha256':compiler['source_sha256']}
            # Entire target is controlled and finite; these three files are never a repository scan.
            directory = directory.resolve(); directory.mkdir()
            (directory/'configs').mkdir()
            raw = {'source': '\ufeffexport const count: number = 7;\r\nexport function next(value: number) { return value + count; }\r\n// 😀\r\n'.encode('utf-8'),
                'root': '\ufeff// private root\r\n{"extends":"./configs/tsconfig.base.json","files":["entry.ts"],}\r\n'.encode('utf-8'),
                'base': '\ufeff// private base\r\n{"compilerOptions":{"strict":true,},}\r\n'.encode('utf-8')}
            paths = {'source':directory/'entry.ts', **{role:directory/row['atlas_path'] for role,row in policy['roster'].items()}}
            for role,path in paths.items(): path.write_bytes(raw[role])
            canonical_inputs = all(project_owned_path_identity(directory,path.relative_to(directory),excluded_roots=[])
                == (True,True) for path in paths.values())
            require(canonical_inputs,'join_input_canonicality')
            reads = dict.fromkeys(paths,0); original_open = Path.open
            def observed_open(path, mode='r', *args, **kwargs):
                for role,target in paths.items():
                    if path == target and mode == 'rb': reads[role] += 1
                return original_open(path,mode,*args,**kwargs)
            with patch.object(Path,'open',observed_open):
                entry = _load_atlas_source_entry({'full_path':str(paths['source'])},owned_root=directory)
                captured = capture_owned_extends_inputs(root=directory)
            capsule = entry['_private_owned_source']
            require(reads == {'source':1,'root':1,'base':1} and capsule.raw == raw['source'], 'join_capture_reads')
            if include_wire_base:
                wire_base, wire_schema = prepare_join_wire_base(profiles, capsule, captured, compiler, invocation, config_invocation)
            capture = {'source_sha256':capsule.source_sha256,'auxiliary_context_sha256':captured.context_sha256,
                'content_reads':reads,'canonical_inputs':canonical_inputs}
            capture_sha = digest(capture); capture['capture_sha256'] = capture_sha
            observed.update(phase='captured_inputs_held',capture=deepcopy(capture))
            # Delete only the three explicitly created fixture files. Subsequent stages
            # must use held bytes, not a reopened physical source/config.
            for path in paths.values(): path.unlink()
            command = [shutil.which('node'),str(ROOT/'tools/tests/fixtures/owned_source_parent_worker.cjs'),
                '--owned-text-worker-jsonl',invocation,attempt,compiler['source_sha256']]
            observed['source_worker_requests'] += 1
            with NodeAstWorkerSession(command,cwd=ROOT) as session:
                if include_wire_base:
                    response_line,transport = session.request_owned(source=capsule,request_id=invocation,timeout_seconds=30,wire_base=wire_base)
                else:
                    response_line,transport = session.request_owned(source=capsule,request_id=invocation,timeout_seconds=30)
            response = json.loads(response_line)
            cost = source_receipt(response,transport,context)
            rows = response['results'][profiles['private_owned_text_bridge']['virtual_file']]
            parser = _file_parser_evidence(rows,'typescript')
            require(parser['status']=='observed','join_parser_not_observed')
            parser_sha = sha(response_line.encode('utf-8'))
            parsed = {'source_sha256':capsule.source_sha256,'parser_response_sha256':parser_sha,
                'parser_status':parser['status'],'counters':cost['counters'],'transport':transport,
                'loader_restored':cost['loader_restored']}
            observed.update(phase='source_parser_observed',parser=deepcopy(parsed))
            symbols = [row for row in _normalize_polyglot_symbols(rows,entry['content'],language='typescript')
                if row['name']!='__file_meta__']
            require({'count','next'}.issubset({row['name'] for row in symbols}), 'join_expected_symbols_missing')
            meta = next(row for row in rows if row['name']=='__file_meta__')
            require(isinstance(meta.get('moduleImports'),list),'join_import_evidence_missing')
            atlas = {'MAIN':{'project':{'name':'PRIVATE_SYNTHETIC','language':'typescript','framework':'none',
                'root':directory.as_posix(),'sequencer_evidence':{'scope':'one_captured_source',
                    'repository_wide_claim':False,'claim_boundary':'Finite synthetic payload only; no collector or whole-project coverage.',
                    'coverage':{'captured_sources':1,'captured_configs':2,'source_worker_requests':1}}},
                'structure':{},'files':{'entry.ts':{'type':'module','language':'typescript',
                    'exports':_extract_exports_for_language(entry['content'],'typescript',symbols),
                    'imports':meta['moduleImports'],'symbols':symbols,'hash':capsule.source_sha256,
                    'canonical_file_path':canonical_inputs,'parser_evidence':parser}},
                'typescript_auxiliary_inputs':json.loads(captured.inventory_json),
                'dependencies':{},'symbols':[],'features':{},'clusters':{},'public_contracts':{}}}
            parent = freeze_source_parent(atlas); commit = json.loads(parent.commit_json)
            parent_receipt = {'atlas':json.loads(parent.atlas_json),'commit':commit,
                'validation_receipt_sha256':parent.validation_sha256,'parser_response_sha256':parser_sha,'hash_override_used':False}
            frozen = {'parent_snapshot_id':commit['snapshot_id'],'atlas_sha256':commit['atlas_sha256'],'frozen':True}
            observed.update(phase='parent_frozen',parent={**frozen,'validation_receipt_sha256':parent.validation_sha256,
                'source_records':1,'source_symbols':len(symbols),'source_exports':len(atlas['MAIN']['files']['entry.ts']['exports'])})
            request,binding = config_request(captured,parent,profiles,compiler,config_invocation)
            frame = (canonical({'request':request,'expected':binding})+'\n').encode('utf-8')
            if include_wire_base:
                observe_owned_wire_config(wire_base, frame, binding, {'snapshot_id': commit['snapshot_id'],
                    'atlas_sha256': commit['atlas_sha256'], 'validation_sha256': parent.validation_sha256,
                    'source_sha256': capsule.source_sha256})
            observed['config_child_launches'] += 1
            child = subprocess.run([shutil.which('node'),str(ROOT/'tools/tests/fixtures/owned_config_extends_harness.cjs'),'native'],
                cwd=ROOT,input=frame,capture_output=True,timeout=30,env=utf8_subprocess_env())
            require(child.returncode==0 and not child.stderr and len(child.stdout)<=profiles[policy['config_limit_profile']]['max_request_bytes'],
                'join_config_child_unavailable')
            output = json.loads(child.stdout); result = output['result']
            require(output['native_config_parser'] is True and output['modeled_only'] is False and
                output['program_attempts']==output['live_io_attempts']==0,'join_config_runtime')
            require(result.pop('repeated_parse_refused') is True and result.pop('disposed_refused') is True,'join_config_lifecycle')
            require(result['status']==policy['success_status'] and result['code']=='' and
                result['strict_observation']=={'base':True,'root_override':None,'effective':True} and
                result['reads']=={'root':1,'base':1} and result['denied_host_calls']==0 and
                result['root_parse_calls']==result['config_chain_calls']==result['cache_entries']==1 and
                result['config_sourcefiles']==2 and result['programs']==result['source_worker_parses']==0,
                'join_config_observation')
            require(not validate_against_schema(ROOT/policy['output_schema'],'observation',result),'join_config_schema')
            require(result['parent_snapshot_id']==commit['snapshot_id'] and result['atlas_sha256']==commit['atlas_sha256'] and
                result['binding_sha256']==digest(binding),'join_config_parent')
            require(payload_sha256(atlas)==commit['atlas_sha256'] and canonical(atlas)==parent.atlas_json,'join_frozen_parent_changed')
            config_receipt = {**{k:frozen[k] for k in ('parent_snapshot_id','atlas_sha256')},
                'config_binding_sha256':digest(binding),'config_invocation_id':config_invocation,'capture_sha256':capture_sha,
                'config_chain_calls':result['config_chain_calls'],'config_sourcefiles':result['config_sourcefiles'],
                'config_programs':result['programs'],'collector_executed':False}
            expected = {**context,'config_invocation_id':config_invocation,'capture_sha256':capture_sha,
                'source_sha256':capsule.source_sha256,'parser_response_sha256':parser_sha,
                'auxiliary_context_sha256':captured.context_sha256,'parent_snapshot_id':commit['snapshot_id'],
                'atlas_sha256':commit['atlas_sha256'],'parent_validation_sha256':parent.validation_sha256,
                'config_binding_sha256':digest(binding)}
            held = hold_preparation_context(expected=expected,policies={'lineage':lineage},
                schemas={'preparation':preparation_schema,'atlas':atlas_schema})
            stages = [{'phase':phase,'invocation_id':invocation,'receipt':receipt} for phase,receipt in zip(
                profiles['private_source_config_parent_preparation']['phase_order'],[capture,parsed,parent_receipt,frozen,config_receipt])]
            preparation = check_join_preparation(stages,expected_context=held)
            if include_wire_base:
                observed['wire_base_measurement'] = project_join_wire_base(wire_base, wire_schema)
            require(preparation['status']=='JOIN_PREPARATION_METADATA_CONFORMANT_NOT_EXECUTED','join_preparation_metadata')
            observed.update(status='PRIVATE_SYNTHETIC_SOURCE_CONFIG_PARENT_JOIN_OBSERVED_NOT_BOUND',phase='config_chain_observed',
                compiler=compiler,config_observation=result,config_binding_sha256=digest(binding),
                preparation_projection=preparation,source_writer_restored=cost['writer_restored'],
                scope='one_synthetic_source_root_base',raw_retained=False,atlas_payload_retained=False,
                basis='caller_observed_native_components_and_declared_metadata_correspondence',
                whole_project_coverage=False,collector_executed=False)
            if include_live_context:
                observed['live_config_context'] = project_join_live_context(atlas=atlas, parent=parent,
                    source_sha256=capsule.source_sha256, binding=binding, observation=result, inputs=live_inputs)
        except Exception as error:
            observed['status']='PRIVATE_SOURCE_CONFIG_JOIN_FAILURE_RETAINED'
            # Error text can originate in arbitrary compilers. Only our finite codes survive.
            code = str(error) if type(error) is ValueError and str(error).startswith('join_') else 'join_operation_unavailable'
            observed['code']=code
            if hasattr(error,'metrics'): observed['transport_failure_metrics']=error.metrics
            if include_wire_base:
                observed['wire_base_measurement'] = project_join_wire_base(wire_base, wire_schema)
            if compiler_plan_inputs is not None:
                observed['compiler_transport_plan'] = {'state': 'NOT_ATTEMPTED', 'code': 'join_compiler_plan_base_failed', 'result': None}
            raise JoinRefused(code,observed) from None
        if compiler_plan_inputs is not None:
            observed['compiler_transport_plan'] = project_join_compiler_plan(pinned=pinned_compiler,
                include_wire_base=include_wire_base, receiver=wire_base,
                receipt=observed.get('wire_base_measurement', {}).get('result'), wire_schema=wire_schema,
                profiles=profiles, compiler=compiler, expected_wire={'source_request_id': invocation,
                    'config_invocation_id': config_invocation, 'binding_sha256': digest(binding),
                    'parent_ref_sha256': digest({'snapshot_id': commit['snapshot_id'], 'atlas_sha256': commit['atlas_sha256'],
                        'validation_sha256': parent.validation_sha256, 'source_sha256': capsule.source_sha256})})
        return observed
    finally:
        if wire_base is not None:
            dispose_owned_wire_base(wire_base)

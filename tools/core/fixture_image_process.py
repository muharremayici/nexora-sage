"""Disabled authored-fixture composition; the shared pipe owner retains IO authority."""
from pathlib import Path
import base64
import hashlib
import json
import math
import os
import posixpath
import subprocess
import tempfile
import time

from tools.core.artifact_validator import _validate
from tools.core.compiler_stream_driver import (
    _PipeExchange, _parse_line, _CompilerStreamDriverRefused,
    _CompilerStreamDriverCancellation,
)
from tools.core.typescript_source_binding import (
    _CompilerStreamEncoder, _semantic_metadata_json as canonical,
    _semantic_metadata_sha as digest, _compiler_stream_stager_manifest,
)

PROFILE = 'private_fixture_image_process'
EVALUATION_PROFILES = ('private_loaded_fixture_image', 'private_compiler_entry_owner', 'compiler_library_inputs',
                       'private_input_image_experiment', 'private_prepared_compiler_image_ingress',
                       'private_compiler_declaration_text_handoff_preparation')
STREAM_PROFILES = ('private_compiler_stream_layout', 'private_compiler_stream_stager', 'private_compiler_stream_js_stager',
                   'private_owned_config_handoff', 'private_owned_text_bridge', 'private_input_image_experiment',
                   'compiler_library_inputs', 'private_compiler_declaration_text_handoff_preparation')


def _need(value, code='fixture_process_context'):
    if not value:
        raise _CompilerStreamDriverRefused(code)


def _size(value):
    return len(canonical(value).encode('utf-8'))


def _expand(packed, schemas):
    """Metadata aliases only; no private runtime capability is reconstructed."""
    support=packed['support'];profiles=support['profiles']
    f={**packed['fixture'],'profiles':{k:profiles[k] for k in EVALUATION_PROFILES},
       'schemas':{k:schemas[k] for k in ['entry','fixture']}}
    image=packed['image'];stream=packed['stream']
    ctx={'binding':image['binding'],'source_context':image['source_context'],
         'policy':profiles['private_input_image_experiment'],
         'admission':{'policy':profiles['private_prepared_compiler_image_ingress'],'schema':schemas['ingress'],
                      'expected':image['admission_expected'],'compiler_manifest':stream['manifest']}}
    return {'fixture':f,'image':{'expected':image['expected'],'context':ctx,'entries':image['entries']},
            'stream':{**stream,'profiles':{k:profiles[k] for k in STREAM_PROFILES},'schema':schemas['stream'],
                      'preparation_schema':schemas['preparation']}}


def _pack(context, descriptor):
    """Share equal metadata once; conflicting or unrepresented fields refuse."""
    f,image,stream=context['fixture'],context['image'],context['stream'];ctx=image['context']
    profiles=dict(f['profiles'])
    for k,v in stream['profiles'].items():
        _need(k not in profiles or canonical(profiles[k])==canonical(v))
        profiles[k]=v
    _need(set(profiles)==set(EVALUATION_PROFILES)|set(STREAM_PROFILES))
    schemas={**f['schemas'],'stream':stream['schema'],'preparation':stream['preparation_schema'],'ingress':ctx['admission']['schema']}
    _need({k:digest(v)for k,v in schemas.items()}=={k:v['canonical_sha256']for k,v in descriptor['schema_sources'].items()})
    packed={'support':{'profiles':profiles,'schemas':{k:digest(v)for k,v in schemas.items()}},
            'fixture':{k:v for k,v in f.items() if k not in ['profiles','schemas']},
            'image':{'expected':image['expected'],'binding':ctx['binding'],'source_context':ctx['source_context'],
                     'admission_expected':ctx['admission']['expected'],'entries':image['entries']},
            'stream':{'expected':stream['expected'],'manifest':stream['manifest']}}
    _need(canonical(_expand(packed, schemas))==canonical(context))
    return packed,schemas


def _image_metadata_upper(context):
    """Overcount shared ancestor records; no namespace/admission decisions here."""
    image = context['image']; ctx = image['context']
    rows = [(r['path'], r['bytes']) for r in image['entries']]
    root = ctx['admission']['policy']['compiler_virtual_root']
    rows += [(root+'/'+r['name'], r['bytes']) for r in context['stream']['manifest']['files']]
    result = _size(ctx['binding'])+_size(ctx['policy'])+_size({
        'binding': ctx['binding'], 'image_policy': ctx['policy'], 'admission': ctx['admission']})
    for path, count in rows:
        _need(path.startswith('/') and path == posixpath.normpath(path) and not path.endswith('/'))
        result += _size({'path': path, 'role': 'auxiliary', 'size_bytes': count,
                         **dict.fromkeys(['raw_sha256', 'parser_sha256', 'host_sha256'], 'f'*64)})
        current, child = posixpath.dirname(path), None
        while True:
            result += _size({'path': current,
                             'files': [posixpath.basename(path)] if child is None else [],
                             'directories': [] if child is None else [posixpath.basename(child)]})
            if current == '/':
                break
            child, current = current, posixpath.dirname(current)
            _need(current.startswith('/'))
    return result


def _reservation(context, descriptor, stream_summary, schema, packed):
    """Named simultaneous representations, explicitly excluding native allocation."""
    m = descriptor['named_forms']; fixture = context['fixture']; image = context['image']
    input_bytes=_size(context)
    c = _size(packed)+_size(descriptor)+_size(schema)
    raw = sum(fixture['role_bytes'].values())+len(fixture['role_bytes'])
    declarations = stream_summary['library_raw_bytes']
    source = sum(r['bytes'] for r in image['entries'])
    frame = stream_summary['largest_frame_jsonl_bytes']
    inventory = _image_metadata_upper(context)
    # Complete copied context encloses every entry/evaluation/bridge/stream input.
    prepare=m['input_prepare_copies']*input_bytes+m['prepare_context_copies']*c
    running=m['input_live_copies']*input_bytes+m['context_copies']*c
    schemas={**context['fixture']['schemas'],'stream':context['stream']['schema'],
             'preparation':context['stream']['preparation_schema'],'ingress':context['image']['context']['admission']['schema']}
    schema_bytes=sum(_size(v)for v in schemas.values())
    raw_max=max(row['raw_bytes']+1 for row in descriptor['schema_sources'].values())
    support=max(m['schema_copies']*schema_bytes,m['schema_startup_raw_forms']*raw_max+m['schema_startup_copies']*schema_bytes)
    metadata = max(prepare,running)+m['output_copies']*descriptor['output_bytes']
    metadata += m['frame_copies']*frame+m['inventory_copies']*inventory+support
    known = metadata+m['entry_forms']*raw+m['declaration_forms']*declarations+m['source_forms']*source
    return {'input_context_bytes':input_bytes,'context_bytes': c, 'support_schema_bytes':schema_bytes,'support_raw_max':raw_max,
            'frame_bytes': frame, 'image_metadata_upper': inventory,
            'metadata_bytes': metadata, 'known_input_bytes': known,
            'output_bytes': descriptor['output_bytes']}


def _prepare(context, expected_completion, profile, schema):
    """No target stat/read. All expectations must precede child acquisition."""
    # First-party contract metadata is distinct from synthetic target acquisition.
    declared = json.loads((Path(__file__).resolve().parents[2]/'config/schemas/fixture_image_process.schema.json').read_text(encoding='utf-8'))
    _need(canonical(schema) == canonical(declared) and profile['metadata_bytes'] == declared['$defs']['policy']['properties']['metadata_bytes']['const'])
    text = canonical({'context': context, 'expected_completion': expected_completion,
                      'profile': profile, 'schema': schema})
    _need(len(text.encode('utf-8')) <= profile['metadata_bytes'], 'fixture_process_budget')
    held = json.loads(text)
    c, p, s = held['context'], held['profile'], held['schema']
    packed,schemas=_pack(c,p)
    errors = []
    for value, definition in [(p, 'policy'), (packed, 'context'), (held['expected_completion'], 'completion')]:
        _validate(value, s['$defs'][definition], s, [definition], errors)
    _need(not errors and p['version'] == 'v1-private-fixture-image-process')
    _need(p['schema_sha256'] == digest(s))
    f, stream, image = c['fixture'], c['stream'], c['image']
    ep, es, e = f['profiles'], f['schemas'], f['expected']
    fixture = ep['private_loaded_fixture_image']; owner = ep['private_compiler_entry_owner']
    _need(p['fixture_policy_sha256'] == digest(fixture) and p['entry_policy_sha256'] == digest(owner))
    _need(fixture['schema_sha256'] == digest(es['fixture']) and owner['schema_sha256'] == digest(es['entry']))
    for value, definition, root in [(fixture, 'policy', es['fixture']), (e, 'expected', es['fixture']),
                                     (image['expected'], 'bridge_expected', es['fixture']),
                                     (f['owner_expected'], 'expected', es['entry'])]:
        _validate(value, root['$defs'][definition], root, [definition], errors)
    _need(not errors)
    _need(all(fixture[k] is False for k in ['default_enabled', 'native_execution_enabled', 'production_enabled', 'semantic_lineage', 'cache_eligible']))
    _need(e['policy_sha256'] == digest(fixture) and e['schema_sha256'] == digest(es['fixture']))
    _need(e['entry']['context_sha256'] == digest(f['owner_expected']))
    _need(e['compile_input_sha256'] == fixture['fixture_source_sha256'] == f['owner_expected']['entry_sha256'])
    _need(f['owner_expected']['compiler_version'] == fixture['fixture_version'])
    _need(e['entry']['entry_sha256'] == e['entry']['text_sha256'] == e['compile_input_sha256'])
    _need(e['node_version'] == fixture['node_version'] and e['api_shape_sha256'] == digest(fixture['api_keys']))
    _need(image['expected'] == {'loaded_expected_sha256': digest(e),
        'image_context_sha256': digest(image['context']), 'source_context_sha256': digest(image['context']['source_context'])})
    _need(e['image_context_sha256'] == digest(image['context']) and e['source_context_sha256'] == digest(image['context']['source_context']))
    boundary, root = f['boundary'], f['root']
    _need(os.path.isabs(boundary) and boundary == os.path.abspath(boundary) and root == os.path.join(boundary, p['root_member']))
    _need(os.path.basename(boundary).startswith(p['boundary_prefix']))
    _need(os.path.commonpath([os.path.abspath(tempfile.gettempdir()), boundary]) == os.path.abspath(tempfile.gettempdir())
          and boundary != os.path.abspath(tempfile.gettempdir()))
    _need(set(f['role_bytes']) == set(p['role_paths']))
    _need(f['role_bytes']['entry'] == f['owner_expected']['entry_bytes'])
    _need(all(type(v) is int and 0 <= v <= ep['compiler_library_inputs']['max_module_bytes'] for v in f['role_bytes'].values()))
    binding = image['context']['binding']; source = image['context']['source_context']
    _need(binding['compiler_source_sha256'] == e['compile_input_sha256'] == source['compiler_source_sha256'])
    _need(binding['compiler_version'] == fixture['fixture_version'] == source['compiler_version'])
    _need(binding['producer_run_id'] == source['invocation_id'] and binding['atlas_snapshot_id'] == source['atlas_snapshot_id']
          and binding['atlas_sha256'] == source['atlas_sha256'])
    _need(len(image['entries']) == 3)
    for row in image['entries']:
        raw = base64.b64decode(row['utf8Base64'], validate=True)
        _need(len(raw) == row['bytes'] and base64.b64encode(raw).decode('ascii') == row['utf8Base64'])
        _need(hashlib.sha256(raw).hexdigest() == row['raw_sha256'])
    _need(source['source_inputs_sha256'] == image['context']['admission']['expected']['source_inputs_sha256'])
    _need(stream['manifest'] == image['context']['admission']['compiler_manifest'])
    _need(digest(stream['preparation_schema']) == ep['private_compiler_declaration_text_handoff_preparation']['schema_sha256'])
    summary = _compiler_stream_stager_manifest(stream['manifest'], stream)
    _need(stream['expected']['compiler_source_sha256'] == e['compile_input_sha256'])
    oracle = held['expected_completion']
    _validate(oracle['image_summary'], image['context']['admission']['schema']['$defs']['summary'],
              image['context']['admission']['schema'], ['image_summary'], errors)
    _need(not errors and oracle['entry_text_sha256'] == e['compile_input_sha256'])
    _need(oracle['image_summary']['content_bytes'] == sum(r['bytes'] for r in image['entries'])+summary['library_raw_bytes'])
    _need(p['metadata_bytes'] <= fixture['limits']['metadata_bytes'] and p['image_bytes'] <= fixture['limits']['image_bytes']
          and p['operation_ms'] <= fixture['limits']['operation_ms'])
    reserve = _reservation(c, p, summary, s, packed)
    if reserve['metadata_bytes'] > p['metadata_bytes'] or reserve['known_input_bytes'] > p['image_bytes']:
        error=_CompilerStreamDriverRefused('fixture_process_budget');error.resource_bounds=reserve;raise error
    held['context']=packed
    held['schemas']=schemas
    return held, reserve


def _run_synthetic_fixture_image_driver(encoder, *, context, expected_completion, profile, schema,
                                        node_path, deadline, cancel=None):
    """One fixed endpoint, one child-local capability chain, no installed mode."""
    process = exchange = outcome = error = held = bootstrap = None
    observation = {'child_started': False, 'child_reaped': True, 'io_threads_remaining': 0,
                   'stdout_clean_eof': False, 'input_half_closed': False, 'frames_written': 0,
                   'acks_accepted': 0, 'stderr_bytes': 0, 'synthetic_only': True}
    start = time.monotonic()
    def read():
        value = _parse_line(exchange)
        if value.get('kind') == 'refused':
            errors = []
            _validate(value, held['schema']['$defs']['refused'], held['schema'], ['refused'], errors)
            _need(not errors, 'fixture_process_result')
            observation['child_refusal'] = {k: value.get(k) for k in ['stage', 'component', 'cleanup_settled']}
            _need(False, 'fixture_process_child_failed')
        return value
    try:
        try:
            try:
                held, reserve = _prepare(context, expected_completion, profile, schema)
                c, p = held['context'], held['profile']; stream = c['stream']; refs = stream['expected']
                _need(type(encoder) is _CompilerStreamEncoder and encoder.snapshot()['state'] == 'READY')
                _need(all(encoder.snapshot()[k] == refs[k] for k in ['context_sha256', 'inventory_sha256', 'manifest_sha256']))
            except (KeyError, TypeError, ValueError, UnicodeError, OverflowError):
                _need(False)
            _need(type(deadline) in (int, float) and math.isfinite(deadline) and start < deadline <= start+p['operation_ms']/1000,
                  'fixture_process_deadline')
            _need(cancel is None or isinstance(cancel, _CompilerStreamDriverCancellation))
            _need(cancel is None or not cancel.is_set(), 'fixture_process_cancelled')
            allowance = math.floor((deadline-time.monotonic())*1000)
            _need(allowance > 0, 'fixture_process_deadline')
            process = subprocess.Popen([str(node_path), str(Path(__file__).with_name('fixture_image_endpoint.cjs')),
                                        str(p['metadata_bytes']), str(allowance)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                                       env={**os.environ, 'NODE_OPTIONS': '', 'NODE_PATH': ''},
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            observation['child_started'] = True
            exchange = _PipeExchange(process, p['output_bytes'], deadline, cancel); exchange.start()
            remaining = math.floor((deadline-time.monotonic())*1000)
            _need(remaining > 0, 'fixture_process_deadline')
            # Never transmit the independently held final-image oracle.
            bootstrap = {'kind': 'bootstrap', 'context': c, 'profile': p, 'schema': held['schema'],
                         'context_sha256': digest(c), 'reservation': reserve, 'remaining_ms': remaining}
            wire = (canonical(bootstrap)+'\n').encode('utf-8')
            _need(len(wire) <= p['metadata_bytes'], 'fixture_process_budget')
            exchange.write(wire); bootstrap = wire = None
            _need(read() == {'kind': 'ready', 'version': p['version'], 'context_sha256': digest(c)}, 'fixture_process_result')
            while encoder.snapshot()['state'] != 'COMPLETE':
                frame = encoder.next_frame(); commit = encoder.snapshot()['state'] == 'COMMIT_PENDING_EOF'
                _need(len(frame) <= reserve['frame_bytes'], 'fixture_process_budget')
                exchange.write(frame, close=commit); observation['frames_written'] += 1; frame = None
                if commit:
                    observation['input_half_closed'] = True; encoder.mark_input_closed()
                encoder.accept_ack(read()); observation['acks_accepted'] += 1
            outcome = read(); errors = []
            _validate(outcome, held['schema']['$defs']['result'], held['schema'], ['result'], errors)
            fixture_schema = held['schemas']['fixture']
            _validate(outcome.get('loaded'), fixture_schema['$defs']['result'], fixture_schema, ['loaded'], errors)
            _need(not errors, 'fixture_process_result')
            _need(outcome['context_sha256'] == digest(c) and outcome['reservation'] == reserve, 'fixture_process_result')
            _need(all(outcome[k] == refs[k] for k in ['session_id', 'manifest_sha256', 'inventory_sha256']), 'fixture_process_result')
            _need(outcome['context_sha256_stream'] == refs['context_sha256'], 'fixture_process_result')
            loaded = outcome['loaded']; oracle = held['expected_completion']
            _need(loaded['image_summary'] == oracle['image_summary'] and loaded['entry_text_sha256'] == oracle['entry_text_sha256'], 'fixture_process_result')
            _need(loaded['loaded_expected_sha256'] == digest(c['fixture']['expected'])
                  and loaded['image_context_sha256'] == digest(_expand(c,held['schemas'])['image']['context'])
                  and loaded['source_context_sha256'] == digest(c['image']['source_context']), 'fixture_process_result')
            _need(loaded['joint_metadata_reserved_bytes'] <= reserve['metadata_bytes']
                  and loaded['known_joint_inputs_reserved_bytes'] <= reserve['known_input_bytes'], 'fixture_process_budget')
            _need(exchange.read()[0] == 'eof', 'fixture_process_trailing')
            observation['stdout_clean_eof'] = True
            try:
                _need(process.wait(timeout=max(0, deadline-time.monotonic())) == 0, 'fixture_process_child_failed')
            except subprocess.TimeoutExpired:
                _need(False, 'fixture_process_deadline')
            exchange._wait(lambda: exchange.stderr_done)
        except BaseException as exc:
            error = exc
            raise
        finally:
            try:
                if type(encoder) is _CompilerStreamEncoder:
                    encoder.dispose()
                try:
                    if exchange is not None:
                        reaped, threads = exchange.cleanup()
                        observation.update(child_reaped=reaped, io_threads_remaining=threads, stderr_bytes=exchange.stderr_bytes)
                    elif process is not None:
                        process.kill()
                        try:
                            process.wait(timeout=max(0, deadline-time.monotonic()))
                        except subprocess.TimeoutExpired:
                            observation['child_reaped'] = process.poll() is not None
                        for stream in [process.stdin, process.stdout, process.stderr]:
                            stream.close()
                except BaseException as cleanup:
                    observation.update(child_reaped=False, cleanup_error=type(cleanup).__name__)
                    if error is not None:
                        raise error
                    raise
            finally:
                held = bootstrap = context = expected_completion = None
                observation['elapsed_ms'] = (time.monotonic()-start)*1000
    except BaseException as error:
        if isinstance(error, _CompilerStreamDriverRefused):
            if hasattr(error,'resource_bounds'):
                observation['resource_bounds']=error.resource_bounds
            error.observation = observation
            if 'child_refusal' in observation:
                error.add_note(canonical(observation['child_refusal']))
        raise
    if not observation['child_reaped'] or observation['io_threads_remaining']:
        error = _CompilerStreamDriverRefused('fixture_process_cleanup'); error.observation = observation; raise error
    if time.monotonic() >= deadline:
        error = _CompilerStreamDriverRefused('fixture_process_deadline'); error.observation = observation; raise error
    return {'result': outcome, 'observation': observation}

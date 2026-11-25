"""Disabled synthetic pipe experiment, not a production compiler transport.

One borrowed encoder, one child, one frame/credit, no retry. Reader and writer
threads are bounded pipe adapters, not encoder owners. No raw stderr is retained.
The caller alone owns the capsule. Cooperative deadlines are not OS isolation.
"""
from pathlib import Path
import hashlib
import json
import math
import os
import subprocess
import threading
import time

from tools.core.artifact_validator import _validate
from tools.core.honesty_telemetry import record_honesty_event
from tools.core.typescript_source_binding import (
    _CompilerStreamEncoder, _CompilerStreamEncoderRefused,
    _semantic_metadata_json, _semantic_metadata_sha,
    _compiler_stream_stager_manifest, _CompilerStreamStagerRefused,
)
from tools.core.prepared_image_process import prepare_context as _image_process_context, project_bootstrap as _image_bootstrap


class _CompilerStreamDriverRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code
        self.observation = None


def _refuse(code):
    raise _CompilerStreamDriverRefused(code)


class _CompilerStreamDriverCancellation:
    """Explicit wakeable cancellation, without a lingering observer thread."""
    def __init__(self):
        self._event = threading.Event()
        self._condition = None

    def cancel(self):
        self._event.set()
        condition = self._condition
        if condition is not None:
            with condition:
                condition.notify_all()

    def is_set(self):
        return self._event.is_set()


class _PipeExchange:
    """One write request and one response slot; all limits come from held policy."""
    def __init__(self, process, ceiling, deadline, cancel):
        self.process, self.ceiling, self.deadline = process, ceiling, deadline
        self.condition = threading.Condition()
        self.stop = False
        self.fault = None
        self.request = self.response = None
        self.write_done = False
        self.stderr_done = False
        self.cancel = cancel
        if cancel is not None:
            cancel._condition = self.condition
        self.stderr_bytes = self.written_bytes = self.write_calls = 0
        self.input_closed = False
        self.threads = []

    def start(self):
        for target in (self._reader, self._writer, self._stderr):
            thread = threading.Thread(target=target, daemon=True)
            self.threads.append(thread)
            thread.start()

    def _fault(self, code):
        with self.condition:
            if self.fault is None and not self.stop:
                self.fault = code
            self.condition.notify_all()

    def _check(self):
        if self.fault:
            if isinstance(self.fault, BaseException):
                raise self.fault
            _refuse(self.fault)
        if self.cancel is not None and self.cancel.is_set():
            _refuse('stream_driver_cancelled')
        if time.monotonic() >= self.deadline:
            _refuse('stream_driver_deadline')

    def _wait(self, predicate):
        with self.condition:
            while True:
                self._check()
                if predicate():
                    return
                self.condition.wait(max(0, self.deadline-time.monotonic()))

    def _put(self, value):
        with self.condition:
            while self.response is not None and not self.stop:
                self.condition.wait(max(0, self.deadline-time.monotonic()))
                if time.monotonic() >= self.deadline:
                    return False
            if self.stop:
                return False
            self.response = value
            self.condition.notify_all()
            return True

    def _reader(self):
        pending = bytearray()
        try:
            while not self.stop:
                newline = pending.find(b'\n')
                if newline >= 0:
                    line = bytes(pending[:newline+1]); del pending[:newline+1]
                    if not self._put(('line', line)):
                        return
                    line = None
                    continue
                if len(pending) >= self.ceiling:
                    self._fault('stream_driver_response_budget'); return
                chunk = os.read(self.process.stdout.fileno(), self.ceiling-len(pending))
                if not chunk:
                    self._put(('incomplete' if pending else 'eof', None)); return
                pending.extend(chunk)
                chunk = None
        except (OSError, ValueError):
            self._fault('stream_driver_pipe')
        except BaseException as exc:
            self._fault(exc)
            record_honesty_event(component='compiler_stream_driver',category='execution_error',operation='read',
                reason='Unexpected synthetic pipe error forwarded unchanged to invocation owner; raw exception omitted.',
                evidence_source='synthetic_fixture',details={'reason':'synthetic fixture IO experiment; no target repository signal'},
                claim_impact='synthetic_driver_not_completed')
        finally:
            pending.clear()

    def _writer(self):
        data = None
        try:
            while True:
                with self.condition:
                    while self.request is None and not self.stop:
                        self.condition.wait()
                    if self.stop:
                        return
                    data, close = self.request
                    self.request = None
                offset = 0
                while offset < len(data):
                    if self.stop:
                        return
                    if time.monotonic() >= self.deadline:
                        self._fault('stream_driver_deadline'); return
                    size = os.write(self.process.stdin.fileno(), memoryview(data)[offset:])
                    if type(size) is not int or size <= 0 or size > len(data)-offset:
                        self._fault('stream_driver_pipe'); return
                    offset += size
                    self.written_bytes += size
                    self.write_calls += 1
                data = None
                if close:
                    self.process.stdin.close()
                    self.input_closed = True
                with self.condition:
                    self.write_done = True
                    self.condition.notify_all()
                if close:
                    return
        except (OSError, ValueError):
            self._fault('stream_driver_pipe')
        except BaseException as exc:
            self._fault(exc)
            record_honesty_event(component='compiler_stream_driver',category='execution_error',operation='write',
                reason='Unexpected synthetic pipe error forwarded unchanged to invocation owner; raw exception omitted.',
                evidence_source='synthetic_fixture',details={'reason':'synthetic fixture IO experiment; no target repository signal'},
                claim_impact='synthetic_driver_not_completed')
        finally:
            data = None

    def _stderr(self):
        try:
            while not self.stop:
                chunk = os.read(self.process.stderr.fileno(), self.ceiling)
                if not chunk:
                    return
                self.stderr_bytes += len(chunk)
                if self.stderr_bytes > self.ceiling:
                    self._fault('stream_driver_stderr_budget'); return
                chunk = None
        except (OSError, ValueError):
            self._fault('stream_driver_pipe')
        except BaseException as exc:
            self._fault(exc)
            record_honesty_event(component='compiler_stream_driver',category='execution_error',operation='stderr',
                reason='Unexpected synthetic pipe error forwarded unchanged to invocation owner; raw exception omitted.',
                evidence_source='synthetic_fixture',details={'reason':'synthetic fixture IO experiment; no target repository signal'},
                claim_impact='synthetic_driver_not_completed')
        finally:
            with self.condition:
                self.stderr_done = True
                self.condition.notify_all()

    def write(self, data, close=False):
        if type(data) is not bytes:
            _refuse('stream_driver_context_invalid')
        with self.condition:
            self._check()
            self.write_done = False
            self.request = (data, close)
            self.condition.notify_all()
        self._wait(lambda: self.write_done)

    def read(self):
        self._wait(lambda: self.response is not None)
        with self.condition:
            value, self.response = self.response, None
            self.condition.notify_all()
            return value

    def cleanup(self):
        if self.cancel is not None:
            self.cancel._condition = None
        with self.condition:
            self.stop = True
            self.request = self.response = None
            self.condition.notify_all()
        # Kill only this invocation's owned child. No resume or second process.
        if self.process.poll() is None:
            try:
                self.process.kill()
            except ProcessLookupError:
                self.process.poll()  # Reconcile an exit racing this child's kill.
        reaped = False
        try:
            self.process.wait(timeout=max(0, self.deadline-time.monotonic()))
            reaped = True
        except subprocess.TimeoutExpired:
            reaped = self.process.poll() is not None
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()
        # Readers use unbuffered descriptors, so no FileIO buffered lock is held.
        for thread in self.threads:
            if thread.ident is not None:
                thread.join(timeout=max(0, self.deadline-time.monotonic()))
        return reaped, sum(thread.is_alive() for thread in self.threads)


def _parse_line(exchange):
    kind, data = exchange.read()
    if kind != 'line':
        _refuse('stream_driver_response_incomplete')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate')
            result[key] = value
        return result
    try:
        result = json.loads(data.decode('utf-8'), object_pairs_hook=pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
        if type(result) is not dict:
            raise ValueError('object')
        return result
    except (ValueError, UnicodeError):
        _refuse('stream_driver_response_invalid')


def _text_process_context(held, errors):
    """Validate metadata with canonical schema/manifest owners, never capture bytes."""
    profiles, schema, expected = (held[k] for k in ('profiles','schema','expected'))
    context = held['text_context']
    composition, preparation, manifest = (context[k] for k in ('composition_schema','preparation_schema','manifest'))
    descriptor = profiles['private_compiler_text_process']
    layout = profiles[descriptor['layout_profile']]
    text_policy = profiles[descriptor['preparation_profile']]
    image = profiles[layout['image_profile']]['limits']; capture = profiles[layout['capture_profile']]
    for value, definition, root in ((descriptor,'driver_policy',composition),(text_policy,'policy',preparation),
                                    (layout,'stream_policy',schema)):
        _validate(value,root['$defs'][definition],root,[definition],errors)
    limits = [image[k] for k in ('compiler_files','compiler_bytes','compiler_file_bytes','image_bytes','metadata_bytes','capture_ms')]
    limits += [capture[k] for k in ('max_files','max_file_bytes','max_total_bytes')]
    if (errors or any(type(n) is not int or n<1 for n in limits)
            or descriptor['schema_sha256'] != _semantic_metadata_sha(composition)
            or text_policy['schema_sha256'] != _semantic_metadata_sha(preparation)
            or layout['schema_sha256'] != _semantic_metadata_sha(schema)
            or expected['schema_sha256'] != layout['schema_sha256']
            or expected['policy_sha256'] != _semantic_metadata_sha(layout)):
        raise ValueError('text process context')
    summary = _compiler_stream_stager_manifest(manifest,held)
    digest = hashlib.sha256()
    for row in manifest['files']:
        digest.update((json.dumps([row['name'],row['bytes'],row['raw_sha256'],row['host_text_sha256']],
                                 separators=(',',':'),ensure_ascii=True)+'\n').encode('utf-8'))
    completion = {'files_completed':summary['library_files'],'raw_bytes_staged':summary['library_raw_bytes'],
                  'prepared_text_summary_sha256':digest.hexdigest()}
    if held['expected_completion'] != completion:
        raise ValueError('independent text completion')
    return {'composition_policy_sha256':_semantic_metadata_sha(descriptor),'composition_schema_sha256':_semantic_metadata_sha(composition),
            'preparation_policy_sha256':_semantic_metadata_sha(text_policy),'preparation_schema_sha256':_semantic_metadata_sha(preparation),
            'image_policy_sha256':_semantic_metadata_sha(profiles[layout['image_profile']])}


def _run_compiler_stream_driver(encoder, *, expected, expected_completion, profiles, schema,
                                node_path, deadline, cancel=None, text_context=None, image_context=None, image_mode=False):
    """Explicit synthetic experiment. Caller-supplied origin is not authenticated.

    Parent deadline covers validation, spawn, bootstrap, writes, ACKs and drain.
    A child receives only remaining duration, never a foreign absolute clock.
    No inventory/raw bytes in argv, environment, temporary files or diagnostics.
    """
    process = exchange = None
    text_mode = text_context is not None
    text_refs = None
    image_refs = None
    image_summary_schema = None
    outcome = None
    error = None
    start = time.monotonic()
    observation = {'status':'SYNTHETIC_PREPARED_BASE_IMAGE_PROCESS_NOT_COMPLETED' if image_mode else 'SYNTHETIC_COMPILER_TEXT_PROCESS_NOT_COMPLETED' if text_mode else 'SYNTHETIC_COMPILER_STREAM_DRIVER_NOT_COMPLETED', 'child_started':False,
        'frames_written':0, 'acks_accepted':0, 'input_half_closed':False, 'stdout_clean_eof':False,
        'child_reaped':True, 'io_threads_remaining':0, 'stderr_bytes':0,
        'real_source_transfer':False, 'compiler_programs':0, 'production_enabled':False,
        'semantic_lineage':False, 'cache_eligible':False}
    try:
        try:
            metadata_ceiling = profiles[profiles['private_compiler_stream_layout']['image_profile']]['limits']['metadata_bytes']
            metadata = {'expected':expected,'expected_completion':expected_completion,'profiles':profiles,'schema':schema}
            if text_mode:
                metadata['text_context'] = text_context
            if image_mode:
                if text_mode:
                    raise ValueError('conflicting synthetic mode')
                metadata['image_context'] = image_context
            text = _semantic_metadata_json(metadata)
            metadata = text_context = image_context = None
            if type(metadata_ceiling) is not int or metadata_ceiling < 1 or len(text.encode('utf-8')) > metadata_ceiling:
                raise ValueError('context')
            held = json.loads(text)
            expected, expected_completion, profiles, schema = (held[k] for k in ('expected','expected_completion','profiles','schema'))
            descriptor = profiles['private_prepared_compiler_image_process' if image_mode else 'private_compiler_text_process' if text_mode else 'private_compiler_stream_driver']
            result_schema = held['image_context']['composition_schema'] if image_mode else held['text_context']['composition_schema'] if text_mode else schema
            layout = profiles[descriptor['layout_profile']]
            image = profiles[layout['image_profile']]['limits']
            wire = profiles[layout['wire_profile']]
            ceiling = min(image['metadata_bytes'], profiles[wire['bridge_profile']]['max_response_bytes'])
            errors = []
            _validate(descriptor,result_schema['$defs']['driver_policy'],result_schema,['driver_policy'],errors)
            _validate(expected,schema['$defs']['stager_expected'],schema,['stager_expected'],errors)
            _validate(expected_completion,result_schema['$defs']['driver_expected_completion'],result_schema,['driver_expected_completion'],errors)
            if (errors or type(ceiling) is not int or ceiling < 1 or len(text.encode('utf-8')) > image['metadata_bytes']
                    or descriptor['schema_sha256'] != _semantic_metadata_sha(result_schema)
                    or expected_completion['files_completed'] > image['compiler_files']
                    or expected_completion['raw_bytes_staged'] > min(image['compiler_bytes'],image['image_bytes'])
                    or not isinstance(encoder,_CompilerStreamEncoder) or encoder.snapshot()['state'] != 'READY'
                    or any(encoder.snapshot()[k] != expected[k] for k in ('context_sha256','inventory_sha256','manifest_sha256'))):
                raise ValueError('context')
            if text_mode:
                text_refs = _text_process_context(held,errors)
            if image_mode:
                image_refs = _image_process_context(held,errors)
                image_summary_schema = held['image_context']['admission']['schema']
        except (KeyError, TypeError, ValueError, UnicodeError, OverflowError, _CompilerStreamStagerRefused):
            _refuse('stream_driver_context_invalid')
        if (type(deadline) not in (int,float) or not math.isfinite(deadline)
                or not start < deadline <= start+image['capture_ms']/1000):
            _refuse('stream_driver_deadline')
        if cancel is not None and not isinstance(cancel,_CompilerStreamDriverCancellation):
            _refuse('stream_driver_context_invalid')
        if cancel is not None and cancel.is_set():
            _refuse('stream_driver_cancelled')
        if time.monotonic() >= deadline:
            _refuse('stream_driver_deadline')
        endpoint = Path(__file__).with_name('compiler_stream_endpoint.cjs')
        allowance = math.floor((deadline-time.monotonic())*1000) if image_mode else (deadline-time.monotonic())*1000
        if image_mode and allowance<=0:
            _refuse('stream_driver_deadline')
        process = subprocess.Popen([str(node_path),str(endpoint),str(image['metadata_bytes']),str(allowance)]
            + (['--prepared-base-image'] if image_mode else ['--declaration-text'] if text_mode else []),
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0,
            env={**os.environ,'NODE_OPTIONS':'','NODE_PATH':''},creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        observation['child_started'] = True
        exchange = _PipeExchange(process,ceiling,deadline,cancel)
        exchange.start()
        remaining_ms=math.floor((deadline-time.monotonic())*1000) if image_mode else (deadline-time.monotonic())*1000
        if image_mode and remaining_ms<=0:
            _refuse('stream_driver_deadline')
        bootstrap = _image_bootstrap(held,remaining_ms) if image_mode else {'kind':'bootstrap','version':descriptor['version'],**held,'remaining_ms':remaining_ms}
        bootstrap_bytes = (_semantic_metadata_json(bootstrap)+'\n').encode('utf-8')
        if len(bootstrap_bytes) > image['metadata_bytes']:
            _refuse('stream_driver_bootstrap_budget')
        exchange.write(bootstrap_bytes)
        bootstrap = bootstrap_bytes = held = text = None
        ready = _parse_line(exchange)
        expected_ready = {'kind':'ready','version':descriptor['version'],'session_id':expected['session_id'],
                          'manifest_sha256':expected['manifest_sha256']}
        if image_mode:
            expected_ready['process_context_sha256']=image_refs['process_context_sha256']
        if ready != expected_ready:
            _refuse('stream_driver_response_invalid')
        while encoder.snapshot()['state'] != 'COMPLETE':
            frame = encoder.next_frame()
            commit = encoder.snapshot()['state'] == 'COMMIT_PENDING_EOF'
            exchange.write(frame,close=commit)
            observation['frames_written'] += 1
            frame = None
            if commit:
                observation['input_half_closed'] = True
                encoder.mark_input_closed()
            encoder.accept_ack(_parse_line(exchange))
            observation['acks_accepted'] += 1
        outcome = _parse_line(exchange)
        errors = []
        _validate(outcome,result_schema['$defs']['driver_result'],result_schema,['driver_result'],errors)
        if image_mode:
            _validate(outcome.get('image_summary'),image_summary_schema['$defs']['summary'],image_summary_schema,['image_summary'],errors)
        if errors or any(outcome[k] != expected[k] for k in ('session_id','manifest_sha256','context_sha256','inventory_sha256')):
            _refuse('stream_driver_response_invalid')
        if any(outcome[k] != expected_completion[k] for k in expected_completion):
            _refuse('stream_driver_response_invalid')
        if text_mode and any(outcome[k] != value for k,value in text_refs.items()):
            _refuse('stream_driver_response_invalid')
        if image_mode and any(outcome[k] != value for k,value in image_refs.items()):
            _refuse('stream_driver_response_invalid')
        if exchange.read()[0] != 'eof':
            _refuse('stream_driver_response_trailing')
        observation['stdout_clean_eof'] = True
        try:
            status = process.wait(timeout=max(0,deadline-time.monotonic()))
        except subprocess.TimeoutExpired:
            _refuse('stream_driver_deadline')
        exchange._check()
        exchange._wait(lambda: exchange.stderr_done)
        if status != 0:
            _refuse('stream_driver_child_failed')
    except _CompilerStreamEncoderRefused:
        error = _CompilerStreamDriverRefused('stream_driver_encoder_refused')
        raise error from None
    except BaseException as exc:
        error = exc
        raise
    finally:
        if isinstance(encoder,_CompilerStreamEncoder):
            encoder.dispose()
        frame = bootstrap = bootstrap_bytes = held = text = text_context = image_context = None
        if exchange is not None:
            reaped, threads = exchange.cleanup()
            observation.update(child_reaped=reaped,io_threads_remaining=threads,stderr_bytes=exchange.stderr_bytes,
                partial_write_calls=exchange.write_calls,pipe_bytes_written=exchange.written_bytes)
        elif process is not None:
            process.kill()
            try:
                process.wait(timeout=max(0,deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                observation['child_reaped'] = process.poll() is not None
            for stream in (process.stdin,process.stdout,process.stderr):stream.close()
        observation['elapsed_ms'] = (time.monotonic()-start)*1000
        if isinstance(error,_CompilerStreamDriverRefused):
            error.observation = dict(observation)
    if not observation['child_reaped'] or observation['io_threads_remaining']:
        refusal = _CompilerStreamDriverRefused('stream_driver_cleanup_unsettled')
        refusal.observation = dict(observation)
        raise refusal
    if time.monotonic() >= deadline:
        refusal = _CompilerStreamDriverRefused('stream_driver_deadline')
        refusal.observation = dict(observation)
        raise refusal
    observation['status'] = descriptor['result_status']
    return {'result':outcome,'observation':observation}


def _run_synthetic_compiler_stream_driver(encoder, *, expected, expected_completion, profiles, schema,
                                          node_path, deadline, cancel=None):
    """Legacy disabled raw mode; its bootstrap/result/wire semantics are unchanged."""
    return _run_compiler_stream_driver(encoder,expected=expected,expected_completion=expected_completion,
        profiles=profiles,schema=schema,node_path=node_path,deadline=deadline,cancel=cancel)


def _run_synthetic_compiler_text_driver(encoder, *, expected, expected_completion, manifest, profiles, schema,
                                       composition_schema, preparation_schema, node_path, deadline, cancel=None):
    """Explicit disabled synthetic text mode. No production/default caller selects it."""
    return _run_compiler_stream_driver(encoder,expected=expected,expected_completion=expected_completion,
        profiles=profiles,schema=schema,node_path=node_path,deadline=deadline,cancel=cancel,
        text_context={'manifest':manifest,'composition_schema':composition_schema,'preparation_schema':preparation_schema})


def _run_synthetic_prepared_image_driver(encoder, *, expected, expected_completion, image_context, profiles, schema,
                                         node_path, deadline, cancel=None):
    """Explicit disabled constructor-double mode; invalid context never selects raw."""
    return _run_compiler_stream_driver(encoder,expected=expected,expected_completion=expected_completion,profiles=profiles,schema=schema,
        node_path=node_path,deadline=deadline,cancel=cancel,image_context=image_context,image_mode=True)

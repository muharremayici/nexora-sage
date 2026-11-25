'use strict';
// One-shot synthetic test adapter, not a transport driver or product command.
const fs = require('node:fs');
const Module = require('node:module');
const crypto = require('node:crypto');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
let forbiddenAttempts = 0, compilerLoads = 0;
const originalLoad = Module._load;
Module._load = function (name, ...args) {
    if (!['../../core/compiler_stream_stager.cjs', 'node:crypto', 'node:perf_hooks', 'node:util'].includes(name)) {
        forbiddenAttempts++; if (String(name).includes('typescript')) compilerLoads++;
        throw new Error('synthetic_module_load_forbidden');
    }
    return originalLoad.call(this, name, ...args);
};
const { createCompilerStreamStager, StreamStagerRefusal } = require('../../core/compiler_stream_stager.cjs');
for (const key of ['readFileSync', 'openSync', 'readdirSync', 'statSync', 'lstatSync', 'realpathSync', 'writeFileSync', 'appendFileSync', 'mkdirSync']) {
    fs[key] = () => { forbiddenAttempts++; throw new Error('synthetic_filesystem_forbidden'); };
}
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const output = { node_version: process.version, forbidden_attempts: 0, compiler_loads: 0, constructed: false,
    events: [], consumer_calls: 0, consumer_result: null, final_snapshot: null };
let stager, lastAck = null, clock = 0, retained = null;
const alloc = Buffer.alloc;
const injected = new RangeError('synthetic allocation failure');
const consumerError = new TypeError('synthetic consumer refusal');
try {
    stager = createCompilerStreamStager(request.expected, request.profiles, request.schema,
        { deadline: request.deadline === undefined ? 20000 : request.deadline, now: () => clock });
    output.constructed = true;
    for (const step of request.steps) {
        if (step.op === 'clock') { clock = step.value === 'nan' ? NaN : step.value; continue; }
        let value = null;
        try {
            if (step.op === 'feed') {
                const bytes = Buffer.from(step.bytes, 'base64');
                if (step.inject_allocation) Buffer.alloc = () => { throw injected; };
                value = stager.feed(bytes); if (value) lastAck = value;
            } else if (step.op === 'release') stager.release_credit(step.ack === undefined ? lastAck : step.ack);
            else if (step.op === 'eof') { value = stager.end_input(); lastAck = value; }
            else if (step.op === 'dispose') stager.dispose();
            else if (step.op === 'caller_metadata') {
                request.expected.session_id = 'changed';
                request.profiles.private_owned_config_handoff.max_request_bytes = 1;
                request.profiles.private_input_image_experiment.limits.capture_ms = 1;
                request.profiles.private_compiler_stream_js_stager.raw_transport_enabled = true;
            }
            else if (step.op === 'serialize') {
                let jsonRefused = false, cloneRefused = false;
                try { JSON.stringify(stager); } catch (error) { jsonRefused = error instanceof TypeError; }
                try { structuredClone(stager); } catch { cloneRefused = true; }
                value = { json_refused: jsonRefused, clone_refused: cloneRefused };
            } else if (step.op === 'consume') {
                const visit = entries => {
                    output.consumer_calls++;
                    if (step.control === 'throw') throw consumerError;
                    const result = entries.map(row => ({ name: row.name, bytes: row.raw.length, sha256: sha(row.raw) }));
                    if (step.control === 'retain') retained = entries[0].raw;
                    if (step.control === 'mutate' && entries[0].raw.length) {
                        const first = entries[0].raw; first[0] ^= 1;
                        output.borrow_mutable = sha(first) !== result[0].sha256 && Object.isFrozen(entries) && Object.isFrozen(entries[0]);
                    }
                    if (step.control === 'expire') clock = 20000;
                    if (step.control === 'promise') return Promise.resolve(result);
                    output.consumer_result = result; return result;
                };
                value = stager.consume(step.control === 'async' ? async entries => visit(entries) : visit);
            } else throw new Error('synthetic_unknown_action');
            output.events.push({ op: step.op, value, snapshot: stager.snapshot() });
        } catch (error) {
            output.events.push({ op: step.op, error: error instanceof StreamStagerRefusal ? error.code : 'UNEXPECTED_ERROR',
                error_kind: error.name, original_error_preserved: error === injected || error === consumerError, snapshot: stager.snapshot() });
            break;
        } finally { Buffer.alloc = alloc; }
    }
} catch (error) {
    output.constructor_error = error instanceof StreamStagerRefusal ? error.code : 'UNEXPECTED_ERROR';
    output.error_kind = error.name;
} finally {
    Buffer.alloc = alloc;
    if (stager) { stager.dispose(); output.final_snapshot = stager.snapshot(); }
    if (retained) output.retained_after_dispose = { bytes: retained.length, sha256: sha(retained) };
    output.forbidden_attempts = forbiddenAttempts; output.compiler_loads = compilerLoads;
}
process.stdout.write(JSON.stringify(output));

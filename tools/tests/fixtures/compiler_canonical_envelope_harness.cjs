"use strict";
const fs = require('node:fs'), Module = require('node:module');
const request = JSON.parse(fs.readFileSync(0, 'utf8')), write = fs.writeSync.bind(fs), loader = Module._load;
const crypto = require('node:crypto');
const window = require('../../core/compiler_text_window.cjs');
const stream = require('../../core/compiler_stream_stager.cjs');
const oracle = stream._canonicalCompilerStreamMetadata, originalCopy = window.copy;
let sharedCalls = 0, blocked = 0, backends = 0, traps = 0, copies = 0, captured = false;
stream._canonicalCompilerStreamMetadata = () => { sharedCalls++; throw new Error('shared_canonical_forbidden'); };
window.copy = (...args) => { copies++; const value = originalCopy(...args); if (copies === 2) captured = true; return value; };
Module._load = function(id, ...args) {
    if (/compiler_entry_owner|compiler_owned_view_plan|compiler_owned_view_basis|compiler_selection_controller|compiler_row_locator|compiler_row_span_association/.test(id)) {
        blocked++; throw new Error('owner_or_consumer_forbidden');
    }
    return loader.call(this, id, ...args);
};
const api = require('../../core/compiler_canonical_envelope.cjs');
const mode = request.mode || 'normal', sentinel = new Error('never_serialize_the_clock_sentinel');
let input = request.input;
if (mode === 'context_order') request.profile = Object.fromEntries(Object.entries(request.profile).reverse());
if (mode === 'context_getter') Object.defineProperty(request.profile, 'schema', {get() { traps++; return 'bad'; }});
if (mode === 'context_proxy') request.profile = new Proxy(request.profile, {ownKeys() { traps++; return []; }});
if (mode === 'number_capacity_getter') Object.defineProperty(request.profile.sublimits, 'number_token_bytes', {get() { traps++; return 32; }});
if (mode === 'number_capacity_proxy') request.profile.sublimits.number_token_bytes = new Proxy({}, {ownKeys() { traps++; return []; }});
if (mode === 'number_capacity_nan') request.profile.sublimits.number_token_bytes = NaN;
if (mode === 'number_capacity_infinity') request.profile.sublimits.number_token_bytes = Infinity;
if (mode === 'getter') Object.defineProperty(input, 'danger', {enumerable: true, get() { traps++; return 1; }});
if (mode === 'proxy') input = new Proxy(input, {ownKeys() { traps++; return []; }});
if (mode === 'cycle') input.loop = input;
if (mode === 'symbol') input[Symbol('extra')] = 1;
if (mode === 'function') input.value = () => { traps++; };
if (mode === 'custom_prototype') Object.setPrototypeOf(input, {custom: true});
if (mode === 'sparse') input = new Array(3);
if (mode === 'array_extra') { input = [1]; input.extra = true; }
if (mode === 'array_accessor') { input = [1]; Object.defineProperty(input, '0', {get() { traps++; return 9; }}); }
if (mode === 'undefined') input.value = undefined;
if (mode === 'bigint') input.value = 1n;
if (mode === 'nan') input.value = NaN;
if (mode === 'infinity') input.value = Infinity;
if (mode === 'negative_zero') input = -0;
if (mode === 'nonenumerable') Object.defineProperty(input, 'hidden', {value: 42});
if (mode === 'null_prototype') input = Object.assign(Object.create(null), input);
if (mode === 'alias') { const child = {x: 1}; input = {a: child, b: child}; }
if (mode === 'deep') { input = 0; for (let i = 0; i < request.depth; i++) input = [input]; }
if (mode === 'invalid_value_surrogate') input = '\ud800';
if (mode === 'invalid_key_surrogate') input = {'\udfff': 1};
if (mode === 'numeric_samples') input = [-0, 0.0000010000000000000002, Number.MAX_VALUE, Number.MIN_VALUE, 1e21, -2.5];
let ticks = 0, mutated = false;
const now = () => {
    ticks++;
    if (mode === 'clock_throw' && ticks === (request.throw_at || 10)) throw sentinel;
    if (mode === 'clock_backwards' && ticks === 10) return -1;
    if (mode === 'clock_nonfinite' && ticks === 10) return Infinity;
    if (mode === 'mutation_after_capture' && captured && !mutated) { input.value = 'changed'; mutated = true; }
    return request.expire_at && ticks >= request.expire_at ? 30000 : 0;
};
const fsNames = ['readFileSync','openSync','statSync','fstatSync','realpathSync','readdirSync','readSync','writeFileSync','appendFileSync'];
for (const name of fsNames) fs[name] = () => { backends++; throw new Error('backend_forbidden'); };
Module._load = () => { blocked++; throw new Error('late_module_forbidden'); };
let result;
try {
    const out = api.encodeSyntheticCanonicalMetadata(input, request.expected, request.profile, request.resources, request.schema,
        {now, deadline: request.deadline ?? 30000});
    const r = JSON.parse(out.receipt.toString('utf8'));
    // Oracle runs outside the operation and uses the captured-clone grammar.
    const reference = mode === 'mutation_after_capture' ? request.oracle_text : oracle(originalCopy(input, 'oracle', 2097152));
    const hash = value => crypto.createHash('sha256').update(value).digest('hex');
    result = {ok: true, receipt: r, payload: out.payload.toString('utf8'), payload_bytes: out.payload.length,
        receipt_bytes: out.receipt.length, receipt_hash_valid: hash(out.receipt) === out.receiptSha256,
        payload_hash_valid: hash(out.payload) === r.payload_sha256, oracle_equal: out.payload.equals(Buffer.from(reference)),
        settlement: out.settlement};
    if (mode === 'mutate_output') { out.payload[0] ^= 1; result.mutated_output_hash_mismatch = hash(out.payload) !== r.payload_sha256; }
} catch (error) {
    result = {ok: false, code: error.code || error.name, observation: api.failureObservation(error), original_error_preserved: error === sentinel};
}
write(1, JSON.stringify({...result, ticks, context_and_payload_copy_calls: copies, shared_canonical_calls: sharedCalls,
    blocked_modules: blocked, backend_attempts: backends, trap_invocations: traps, mutated}));

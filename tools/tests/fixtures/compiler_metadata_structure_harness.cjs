"use strict";
const fs = require('node:fs'), Module = require('node:module'), crypto = require('node:crypto');
const request = JSON.parse(fs.readFileSync(0, 'utf8')), write = fs.writeSync.bind(fs), loader = Module._load;
const mode = request.mode || 'normal', sentinel = new Error('never_serialize_structure_sentinel');
let backends = 0, blocked = 0, encoders = 0, traps = 0, copies = 0, allocations = 0, copied = false, mutated = false;
const bufferAlloc = Buffer.alloc, typed = Object.getPrototypeOf(Uint8Array.prototype), originalSet = typed.set;
Buffer.alloc = (...args) => { allocations++; if (mode === 'alloc_throw') throw sentinel; return bufferAlloc(...args); };
typed.set = function(...args) {
    copies++; if (mode === 'copy_throw') throw sentinel;
    const result = originalSet.apply(this, args); copied = true; return result;
};
const canonical = require('../../core/compiler_canonical_envelope.cjs');
canonical.encodeSyntheticCanonicalMetadata = () => { encoders++; throw new Error('encoder_forbidden'); };
Module._load = function(id, ...args) {
    if (/compiler_entry_owner|compiler_owned_view|compiler_selection_controller|compiler_row_|typescript\.js/.test(id)) {
        blocked++; throw new Error('owner_or_compiler_forbidden');
    }
    return loader.call(this, id, ...args);
};
const api = require('../../core/compiler_metadata_structure.cjs');
let input = Buffer.from(request.hex, 'hex');
if (mode === 'shadow_properties') {
    for (const k of ['length','byteLength','buffer','byteOffset','copy','valueOf','toJSON',Symbol.iterator])
        Object.defineProperty(input, k, {get() { traps++; throw sentinel; }});
}
if (mode === 'proxy' || mode === 'revoked') {
    const handler = {get() { traps++; throw sentinel; }, getPrototypeOf() { traps++; throw sentinel; }, ownKeys() { traps++; throw sentinel; }};
    if (mode === 'revoked') { const r = Proxy.revocable(input, handler); input = r.proxy; r.revoke(); }
    else input = new Proxy(input, handler);
}
if (mode === 'subclass') { class Bytes extends Uint8Array {} input = new Bytes(input); }
if (mode === 'uint8') input = new Uint8Array(input);
if (mode === 'shared') { const b = Buffer.from(new SharedArrayBuffer(input.length)); originalSet.call(b, input); input = b; }
if (mode === 'resizable') { const b = new ArrayBuffer(input.length, {maxByteLength: input.length * 2}); const v = Buffer.from(b); originalSet.call(v, input); input = v; }
if (mode === 'detached') { const b = new ArrayBuffer(input.length); input = Buffer.from(b); structuredClone(b, {transfer: [b]}); }
if (mode === 'string') input = request.hex;
if (mode === 'object') input = {length: 80, valueOf() { traps++; throw sentinel; }};
if (mode === 'context_getter') Object.defineProperty(request.profile, 'model', {get() { traps++; throw sentinel; }});
if (mode === 'context_proxy') request.profile = new Proxy(request.profile, {ownKeys() { traps++; throw sentinel; }});
if (mode === 'context_order') request.profile = Object.fromEntries(Object.entries(request.profile).reverse());
let ticks = 0;
const now = () => {
    ticks++;
    if (mode === 'clock_throw' && ticks === (request.throw_at || 10)) throw sentinel;
    if (mode === 'clock_backwards' && ticks === 10) return -1;
    if (mode === 'clock_nonfinite' && ticks === 10) return Infinity;
    if (mode === 'mutation_after_copy' && copied && !mutated) { input[64] = 3; mutated = true; }
    return request.expire_at && ticks >= request.expire_at ? 30000 : 0;
};
for (const k of ['readFileSync','openSync','statSync','fstatSync','realpathSync','readdirSync','readSync','writeFileSync','appendFileSync'])
    fs[k] = () => { backends++; throw new Error('backend_forbidden'); };
Module._load = () => { blocked++; throw new Error('late_module_forbidden'); };
const stringify = JSON.stringify;
if (mode === 'receipt_throw' || mode === 'observation_throw') JSON.stringify = value => {
    if (value?.status === 'STRUCTURE_LAYOUT_VERIFIED_NOT_METADATA_CONTENT_OR_OWNER') throw sentinel;
    if (mode === 'observation_throw' && value?.structure_returned === false) throw new Error('secondary');
    return stringify(value);
};
let result;
try {
    const out = api.validateSyntheticMetadataStructure(input, request.expected, request.profile, request.resources,
        request.schema, {now, deadline: request.deadline ?? 30000});
    const receipt = JSON.parse(out.receipt.toString('utf8'));
    const hash = data => crypto.createHash('sha256').update(data).digest('hex');
    result = {ok: true, receipt, receipt_bytes: out.receipt.length, receipt_hash_valid: hash(out.receipt) === out.receiptSha256,
        returned_fields: Object.keys(out), settlement: out.settlement};
    if (mode === 'mutate_receipt') { out.receipt[0] ^= 1; result.mutation_hash_mismatch = hash(out.receipt) !== out.receiptSha256; }
} catch (error) {
    result = {ok: false, code: error.code || error.name, observation: api.failureObservation(error), original_error_preserved: error === sentinel};
}
JSON.stringify = stringify;
write(1, stringify({...result, ticks, backend_attempts: backends, blocked_modules: blocked, encoder_calls: encoders,
    trap_invocations: traps, buffer_allocations: allocations, private_copy_calls: copies, mutated}));

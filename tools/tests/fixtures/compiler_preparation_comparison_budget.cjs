'use strict';
// Supplied-authored data only. No installed compiler or target is loaded.
const fs = require('node:fs'), Module = require('node:module');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const shared = require('../../core/compiler_stream_stager.cjs');
const window = require('../../core/compiler_text_window.cjs');
const originalCopy = window.copy, load = Module._load;
let copies = 0, traps = 0, forbidden = 0;
window.copy = function(...args) { copies++; return originalCopy(...args); };
Module._load = function(name, ...args) {
    if (name !== './compiler_text_window.cjs') { forbidden++; throw new Error('late_module_forbidden'); }
    return load.call(this, name, ...args);
};
for (const k of ['readFileSync', 'readFile', 'openSync', 'open', 'statSync', 'readdirSync', 'writeFileSync', 'appendFileSync']) {
    fs[k] = () => { forbidden++; throw new Error('filesystem_forbidden'); };
}
const sites = ['d', 't', 'i', 'a', 'i.expected_context', 'i.expected_context.entry_expected'];
const VERSION = 'v1-private-supplied-synthetic-comparison-budget', CUT = 'six_supplied_synthetic_roots';
const binding = { invocation_id: 'a'.repeat(64), tree_sha256: 'b'.repeat(64), source_sha256: 'c'.repeat(64), cut: CUT };
const caps = { version: VERSION, authority_kind: 'independent_declaration', invocation_id: binding.invocation_id,
    source_sha256: binding.source_sha256, cut: CUT, fuel: request.fuel ?? 1000, payload: request.payload ?? 10000000 };
let supplied = request.roots, declaredBinding = { ...binding };
if (request.cap_change) Object.assign(caps, request.cap_change);
if (request.binding_change) Object.assign(declaredBinding, request.binding_change);
if (request.metadata_hook === 'caps_getter') Object.defineProperty(caps, 'fuel', { enumerable: true, get() { traps++; throw Error('getter'); } });
if (request.metadata_hook === 'binding_proxy') declaredBinding = new Proxy(declaredBinding, { get() { traps++; throw Error('proxy'); } });
if (request.metadata_hook === 'root_getter') Object.defineProperty(supplied.d, 'value', { enumerable: true, get() { traps++; throw Error('getter'); } });
if (request.metadata_hook === 'root_proxy') supplied = new Proxy(supplied, { ownKeys() { traps++; throw Error('proxy'); } });
if (request.metadata_hook === 'root_cycle') supplied.d.value = supplied;
let api, peer, constructionError = null;
try { api = shared._createSyntheticPreparationComparison(supplied, caps, declaredBinding); }
catch (error) { constructionError = { code: error.code ?? null, constructor: error.constructor.name }; }
const output = { constructed: !!api, construction_error: constructionError, copy_calls: null,
    traps: null, forbidden_calls: null, native: { from: 0, compare: 0 }, events: [], snapshot: null,
    compiler_loaded: false, spy_scope: 'adapter_validate_root_only' };
if (api && request.stager) {
    // Exercise both ordinary lexical equality callers while a zero-credit
    // private context exists. No product/test callback seam is introduced.
    const before = JSON.stringify(api.snapshot()), r = request.stager;
    const stager = shared.createCompilerStreamStager(r.expected, r.profiles, r.schema, { deadline: 20000, now: () => 0 });
    let ack = null, consumers = 0;
    for (const step of r.steps) {
        if (step.op === 'feed') ack = stager.feed(Buffer.from(step.bytes, 'base64')) || ack;
        else if (step.op === 'release') stager.release_credit(ack);
        else if (step.op === 'eof') ack = stager.end_input();
        else if (step.op === 'consume') stager.consume(entries => { consumers++; return entries.map(row => ({ name: row.name, bytes: row.raw.length })); });
        else throw new Error('unknown_authored_stager_step');
    }
    output.ordinary_stager = { consumers, state: stager.snapshot().state,
        private_context_unchanged: before === JSON.stringify(api.snapshot()) };
    output.snapshot = api.seal(); output.context_closed = api.context === null;
} else if (api) {
    let handle = api.context, current = { ...binding };
    if (request.current_binding_field) current[request.current_binding_field] = 'd'.repeat(64);
    if (request.bad_handle === 'foreign') {
        peer = shared._createSyntheticPreparationComparison(supplied, caps, declaredBinding);
        handle = peer.context;
    } else if (request.bad_handle === 'fake') handle = {};
    else if (request.bad_handle === 'index') handle = 1;
    else if (request.bad_handle === 'null') handle = null;
    if (request.mutate) {
        supplied.d.value = 'caller_changed'; caps.fuel = 999999; caps.payload = 999999999;
    }
    if (request.ordinary) {
        const before = JSON.stringify(api.snapshot());
        output.ordinary = shared._canonicalCompilerStreamMetadata({ z: 2, a: 1 }, handle,
            new Proxy({}, { get() { traps++; throw Error('extra'); } }));
        shared._validateCompilerStreamValue(1, { const: 1 }, {}, 'caller_invalid', handle);
        output.ordinary_did_not_charge = before === JSON.stringify(api.snapshot());
    }
    if (request.serialize) {
        try { JSON.stringify(api.context); output.context_json_refused = false; }
        catch { output.context_json_refused = true; }
        try { structuredClone(api.context); output.context_clone_refused = false; }
        catch { output.context_clone_refused = true; }
        try { JSON.stringify(api); output.adapter_json_refused = false; }
        catch { output.adapter_json_refused = true; }
    }
    const from = Buffer.from, compare = Buffer.compare, freeze = Object.freeze;
    const nativeError = new RangeError('never_serialize_native_details');
    const proxyError = new Proxy(nativeError, { get() { traps++; throw Error('error hook'); }, getPrototypeOf() { traps++; throw Error('error hook'); } });
    const original = request.proxy_error ? proxyError : nativeError;
    let reentered = false, freezeFailures = 0;
    const observe = (site, operation) => {
        try { operation(); return { site, ok: true }; }
        catch (error) { return { site, ok: false, original_identity: error === original,
            code: error === original ? null : error.code ?? null }; }
    };
    Buffer.from = function(...args) {
        output.native.from++;
        if (request.reentry && !reentered) {
            reentered = true;
            output.reentry = observe('nested', () => api.validate_root(handle, 'd', current));
        }
        if (request.fault === 'first' && output.native.from === 1
            || request.fault === 'second' && output.native.from === 2) throw original;
        return from(...args);
    };
    Buffer.compare = function(...args) {
        output.native.compare++;
        if (request.fault === 'compare') throw original;
        return compare(...args);
    };
    Object.freeze = function(value) {
        if (request.snapshot_fault && freezeFailures === 0 && value?.scope === 'synthetic_comparison_completed_prefix') {
            freezeFailures++; throw nativeError;
        }
        return freeze(value);
    };
    try {
        const runSites = request.action === 'early_seal' || request.ordinary ? []
            : request.bad_site ? [request.bad_site] : request.action === 'first' ? ['d'] : sites;
        for (const site of runSites) {
            const event = observe(site, () => api.validate_root(handle, site, current));
            output.events.push(event);
            if (!event.ok) break;
        }
        if (request.repeat && output.events[0]?.ok) output.events.push(observe('repeat', () => api.validate_root(handle, 'd', current)));
        if (request.retry && output.events.some(e => !e.ok)) {
            const before = JSON.stringify(api.snapshot());
            output.retry = observe('retry', () => api.validate_root(handle, 'd', current));
            output.retry_did_not_charge = before === JSON.stringify(api.snapshot());
        }
    } finally { Buffer.from = from; Buffer.compare = compare; Object.freeze = freeze; }
    output.freeze_failures = freezeFailures;
    output.before_seal = api.snapshot();
    output.snapshot = api.seal(request.loss ? false : true);
    output.context_closed = api.context === null;
    output.idempotent_seal = JSON.stringify(api.seal()) === JSON.stringify(output.snapshot);
    if (peer) { output.peer_unchanged = peer.snapshot().phase === 'OPEN' && peer.snapshot().counters.entered === 0; peer.seal(); }
}
output.copy_calls = copies; output.traps = traps; output.forbidden_calls = forbidden;
process.stdout.write(JSON.stringify(output));

'use strict';
// Synthetic source data and explicit host-fault observation only; no target/compiler.
const fs = require('node:fs'), Module = require('node:module');
const req = JSON.parse(fs.readFileSync(0, 'utf8'));
const owner = require('../../core/compiler_preparation_capture.cjs');
const window = require('../../core/compiler_text_window.cjs');
const load = Module._load, copy = window.copy, freeze = Object.freeze, descriptors = Object.getOwnPropertyDescriptors;
const CUT = 'six_owner_shaped_supplied_synthetic_roots';
const binding = { invocation_id: 'a'.repeat(64), tree_sha256: 'b'.repeat(64), source_sha256: 'c'.repeat(64), cut: CUT };
const out = { copies: 0, traps: 0, forbidden_calls: 0, faults: 0, events: [], snapshot: null,
    constructor_returned: false, compiler_loaded: false, compiler_or_target_operations: 0 };
let lease = null, peer = null, captured = null, view = null, witness = null, reentered = false;
const original = req.error_kind === 'primitive' ? 'native_primitive'
    : req.error_kind === 'proxy' ? new Proxy({}, { get() { out.traps++; throw Error('error_inspected'); },
        getPrototypeOf() { out.traps++; throw Error('error_inspected'); } }) : new RangeError('synthetic_native_failure');
const event = (site, fn) => {
    try { return { site, ok: true, value: fn() }; }
    catch (error) { return { site, ok: false, original_identity: error === original,
        code: error === original ? null : owner._preparationCaptureFailureCode(error) }; }
};
const observe = (site, fn) => { const result = event(site, fn); delete result.value; out.events.push(result); return result; };
window.copy = function(...args) {
    out.copies++; captured = copy(...args);
    if (req.fault === 'copy') { out.faults++; throw original; }
    return captured;
};
Module._load = function(name, ...args) {
    if (!['./compiler_text_window.cjs'].includes(name)) { out.forbidden_calls++; throw Error('forbidden'); }
    return load.call(this, name, ...args);
};
const reenter = () => {
    reentered = true;
    observe('reentry', () => req.reentry === 'release' ? lease.release()
        : req.reentry === 'take' ? lease.take_for_comparison(witness, binding) : lease.snapshot());
};
Object.freeze = function(value) {
    const stage = value === captured ? 'tree' : value?.outcome === 'returned' ? 'receipt'
        : value?.held === captured && Object.hasOwn(value, 'witness') ? 'borrow'
        : value?.version === 'v1-private-source-owned-preparation-capture' ? 'snapshot'
        : typeof value?.take_for_comparison === 'function' ? 'api'
        : typeof value?.toJSON === 'function' ? 'witness' : null;
    if (req.fault === stage && out.faults === 0) { out.faults++; throw original; }
    if (req.reentry_stage === stage && !reentered && lease) reenter();
    if (req.construct_release && stage === 'api') value.release();
    return freeze(value);
};
Object.getOwnPropertyDescriptors = function(value) {
    if (req.reentry_stage === 'binding' && value === binding && lease && !reentered) reenter();
    return descriptors(value);
};
let input = req.owner_input;
if (req.raw === 'getter') Object.defineProperty(input, 'intent', { enumerable: true, get() { out.traps++; throw Error('getter'); } });
if (req.raw === 'proxy') input = new Proxy(input, { ownKeys() { out.traps++; throw Error('proxy'); } });
if (req.raw === 'cycle') input.intent.loop = input;
if (req.raw === 'symbol') input[Symbol('hidden')] = 1;
if (req.raw === 'sparse') input.intent.values = Array(2);
if (req.raw === 'prototype') Object.setPrototypeOf(input.intent, Date.prototype);
let initialBinding = { ...binding };
if (req.initial_binding === 'missing') delete initialBinding.tree_sha256;
if (req.initial_binding === 'extra') initialBinding.extra = true;
if (req.initial_binding === 'invalid') initialBinding.source_sha256 = 'invalid';
if (req.initial_binding === 'getter') Object.defineProperty(initialBinding, 'cut', { get() { out.traps++; throw Error('getter'); } });
if (req.initial_binding === 'proxy') initialBinding = new Proxy(initialBinding, { ownKeys() { out.traps++; throw Error('proxy'); } });
if (req.initial_binding === 'cut') initialBinding.cut = 'other';
try {
    const args = [input, initialBinding];
    if (req.constructor_args === 'extra') args.push(() => { out.traps++; });
    if (req.constructor_args === 'missing') args.pop();
    const constructed = event('constructor', () => owner._capturePreparation(...args));
    out.events.push({ ...constructed, value: undefined });
    if (constructed.ok) {
        lease = constructed.value; out.constructor_returned = true; witness = lease.witness;
        if (req.initial_binding) throw Error('unexpected_constructor');
        if (req.fault !== 'snapshot' && req.reentry_stage !== 'snapshot') out.initial_snapshot = lease.snapshot();
        if (req.foreign) { peer = owner._capturePreparation(input, binding); out.peer_before = JSON.stringify(peer.snapshot()); }
        if (req.serialize) {
            out.serialization = {};
            for (const [key, value] of Object.entries({ lease, witness })) {
                out.serialization[key] = !event('serialize', () => JSON.stringify(value)).ok;
                out.serialization[key + '_clone'] = !event('clone', () => structuredClone(value)).ok;
            }
        }
        if (req.mutate) { input.intent.z = 'mutated_after_capture'; initialBinding.source_sha256 = 'd'.repeat(64); }
        if (req.action === 'release') { lease.release(); lease.release(); }
        else if (req.action === 'snapshot') observe('snapshot', () => req.extra ? lease.snapshot(() => { out.traps++; }) : lease.snapshot());
        else if (req.action === 'bad_release') observe('release', () => lease.release(() => { out.traps++; }));
        else {
            if (req.action === 'closed_take') lease.release();
            let handle = req.foreign ? peer.witness : req.fake ? {} : witness;
            const current = { ...binding };
            if (req.stale) current[req.stale] = 'd'.repeat(64);
            if (req.current_binding === 'extra') current.extra = true;
            if (req.current_binding === 'getter') Object.defineProperty(current, 'cut', { get() { out.traps++; throw Error('getter'); } });
            const args = [handle, req.reentry_stage === 'binding' ? binding : current];
            if (req.take_args === 'extra') args.push(() => { out.traps++; });
            if (req.take_args === 'missing') args.pop();
            const borrowed = event('borrow', () => lease.take_for_comparison(...args));
            out.events.push({ ...borrowed, value: undefined });
            if (borrowed.ok) {
                view = borrowed.value;
                const actual = req.foreign ? null : captured;
                out.same_top_identity = view.held === actual;
                out.same_witness = view.witness === witness;
                out.same_receipt = view.copy_charges === out.initial_snapshot?.copy_charges;
                const frozen = v => !v || typeof v !== 'object' || Object.isFrozen(v) && Object.values(v).every(frozen);
                out.tree_and_view_frozen = Object.isFrozen(view) && frozen(view.held);
                out.captured_intent_z = view.held.intent.z;
                out.mutation_refused = !event('mutate', () => { view.held.intent.z = 999; }).ok;
                out.view_serialization_refused = !event('serialize', () => JSON.stringify(view)).ok;
                if (req.action === 'repeat') observe('repeat', () => lease.take_for_comparison(witness, binding));
                lease.release();
                out.borrowed_reference_survives_release = view.held.intent.z === out.captured_intent_z && frozen(view.held);
            }
        }
        if (req.retry) observe('retry', () => lease.take_for_comparison(witness, binding));
        if (peer) out.peer_unchanged = out.peer_before === JSON.stringify(peer.snapshot());
    }
} finally {
    Object.freeze = freeze; Object.getOwnPropertyDescriptors = descriptors; window.copy = copy; Module._load = load;
    if (lease) {
        out.snapshot = lease.snapshot(); out.witness_cleared = lease.witness === null;
        lease.release(); out.after_release = lease.snapshot();
    }
    if (peer) peer.release();
}
process.stdout.write(JSON.stringify(out));

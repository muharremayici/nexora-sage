'use strict';
// Only supplied data and controlled host faults; no target, compiler or producer.
const fs = require('node:fs'), Module = require('node:module');
const req = JSON.parse(fs.readFileSync(0, 'utf8'));
const owner = require('../../core/compiler_preparation_capture.cjs'), window = require('../../core/compiler_text_window.cjs');
const load = Module._load, copy = window.copy, freeze = Object.freeze, OriginalSet = global.Set;
const out = { copies: 0, traps: 0, forbidden_calls: 0, faults: 0, events: [], order: [], budgets: [],
    observation_allocations: 0, tree_freezes: 0, compiler_loaded: false, compiler_or_target_operations: 0 };
let api = null, peer = null, captured = null, mainCapture = null, view = null, witness = null, observedReceipt = null, armed = false, reentered = false;
const nodes = new WeakSet();
const original = req.error_kind === 'primitive' ? 'native_primitive' : req.error_kind === 'proxy'
    ? new Proxy({}, { get() { out.traps++; throw Error('inspected'); }, getPrototypeOf() { out.traps++; throw Error('inspected'); } })
    : new RangeError('synthetic_native_failure');
const result = fn => { try { return { ok: true, value: fn() }; } catch (error) {
    return { ok: false, original_identity: error === original, code: error === original ? null : owner._preparationCaptureFailureCode(error) };
} };
const observe = (site, fn) => { const r = result(fn); out.events.push({ ...r, value: undefined, site }); return r; };
const collect = value => { if (value && typeof value === 'object') { nodes.add(value); for (const v of Object.values(value)) collect(v); } };
const reenter = () => {
    reentered = true;
    observe('reentry', () => req.reentry === 'release' ? api.release() : req.reentry === 'copy' ? api.copy_input(req.owner_input)
        : req.reentry === 'borrow' ? api.take_for_context_owner(witness) : api.snapshot());
};
window.copy = function(...args) {
    out.copies++; out.order.push('copy');
    const budget = args[5], row = { before: { nodes: budget.nodes, bytes: budget.bytes } }; out.budgets.push(row);
    try {
        captured = copy(...args); collect(captured);
        if (armed && req.reentry_stage === 'copy' && !reentered) reenter();
        if (armed && req.fault === 'copy') { out.faults++; throw original; }
        return captured;
    } finally { row.after = { nodes: budget.nodes, bytes: budget.bytes }; }
};
Module._load = function(name, ...args) {
    if (name !== './compiler_text_window.cjs') { out.forbidden_calls++; throw Error('forbidden'); }
    return load.call(this, name, ...args);
};
Object.freeze = function(value) {
    const stage = typeof value?.copy_input === 'function' ? 'api' : value && typeof value === 'object' && nodes.has(value) ? 'tree'
        : Object.hasOwn(value, 'nodes') && Object.hasOwn(value, 'outcome') ? 'observation'
        : Object.hasOwn(value, 'held') && Object.hasOwn(value, 'witness') ? 'borrow'
        : value?.version === 'v1-private-phased-preparation-copy-attempt' ? 'snapshot'
        : typeof value?.toJSON === 'function' ? 'witness' : 'other';
    if (armed || stage === 'api') {
        out.order.push(stage);
        if (stage === 'tree') out.tree_freezes++;
        if (stage === 'observation') { out.observation_allocations++; out.frozen_at_observation = captured !== null && Object.isFrozen(captured); }
        if (req.fault === stage && out.faults === 0) { out.faults++; throw original; }
        if (api && req.reentry_stage === stage && !reentered) reenter();
        if (stage === 'api' && req.construct_action) {
            observe('construction_hook', () => req.construct_action === 'release' ? value.release() : value.copy_input(req.owner_input));
        }
    }
    return freeze(value);
};
let input = req.owner_input;
if (req.raw === 'getter') Object.defineProperty(input.intent, 'late', { enumerable: true, get() { out.traps++; throw Error('getter'); } });
if (req.raw === 'proxy') input.intent = new Proxy(input.intent, { ownKeys() { out.traps++; throw Error('proxy'); } });
if (req.raw === 'cycle') input.intent.loop = input;
if (req.raw === 'depth') { let p = {}; input.intent.deep = p; for (let i = 0; i < 34; i++) { p.next = {}; p = p.next; } }
if (req.raw === 'bytes') input.intent = 'x'.repeat(65537);
if (req.raw === 'nodes') input = { intent: Array(65530).fill(''), anchors: { a: null }, profiles: { b: null }, schemas: {} };
try {
    const made = observe('constructor', () => req.constructor_arg ? owner._createPreparationCopyAttempt(() => { out.traps++; }) : owner._createPreparationCopyAttempt());
    if (made.ok) {
        api = made.value; out.initial = api.snapshot(); armed = true;
        if (req.fault === 'setup') global.Set = function() { out.faults++; throw original; };
        const early = { observe_before_copy: () => api.copy_observation(), freeze_before_copy: () => api.freeze_owned(),
            take_before_copy: () => api.take_for_context_owner({}), snapshot_extra: () => api.snapshot(true),
            release_extra: () => api.release(true), copy_extra: () => api.copy_input(input, true) };
        if (Object.hasOwn(early, req.boot)) observe(req.boot, early[req.boot]);
        else {
            const copied = observe('copy', () => api.copy_input(input)); mainCapture = captured;
            if (req.boot === 'copy_twice') observe('copy_twice', () => api.copy_input(input));
            else {
                if (!req.skip_observation && !req.freeze_first) {
                    const r = observe('observation', () => req.boot === 'observation_extra' ? api.copy_observation(true) : api.copy_observation());
                    if (r.ok) observedReceipt = r.value;
                }
                if (req.boot === 'retry_gap_before_freeze') observe('retry_gap_before_freeze', () => api.copy_observation());
                if (copied.ok) {
                    observe('freeze', () => req.boot === 'freeze_extra' ? api.freeze_owned(true) : api.freeze_owned());
                    if (!req.skip_observation && req.freeze_first) { const r = observe('observation', () => api.copy_observation()); if (r.ok) observedReceipt = r.value; }
                    if (req.boot === 'observe_twice') observe('observe_twice', () => api.copy_observation());
                    if (req.boot === 'freeze_twice') observe('freeze_twice', () => api.freeze_owned());
                    const token = observe('witness', () => api.witness); witness = token.value;
                    if (req.serialize && token.ok) {
                        out.serialization = {};
                        for (const [key, value] of Object.entries({ api, witness })) {
                            out.serialization[key] = !result(() => JSON.stringify(value)).ok;
                            out.serialization[key + '_clone'] = !result(() => structuredClone(value)).ok;
                        }
                    }
                    if (req.foreign) {
                        peer = owner._createPreparationCopyAttempt(); peer.copy_input(req.owner_input); peer.copy_observation(); peer.freeze_owned();
                        out.peer_before = JSON.stringify(peer.snapshot());
                    }
                    if (req.boot === 'closed_take') api.release();
                    if (token.ok && witness !== null && witness !== undefined) {
                        const handle = req.fake ? {} : req.foreign ? peer.witness : witness;
                        const borrowed = observe('borrow', () => req.boot === 'take_extra' ? api.take_for_context_owner(handle, () => { out.traps++; }) : api.take_for_context_owner(handle));
                        if (borrowed.ok) {
                            view = borrowed.value;
                            const frozen = v => !v || typeof v !== 'object' || Object.isFrozen(v) && Object.values(v).every(frozen);
                            out.same_top = view.held === mainCapture; out.same_witness = view.witness === witness;
                            out.all_frozen = frozen(view.held) && Object.isFrozen(view);
                            out.view_charges = view.copy_charges; out.same_receipt = view.copy_charges === observedReceipt;
                            if (req.boot === 'take_twice') observe('take_twice', () => api.take_for_context_owner(witness));
                            api.release(); out.borrowed_reference_survives = view.held === mainCapture && frozen(view.held);
                        }
                    }
                } else if (req.freeze_failed_copy) observe('freeze_failed_copy', () => api.freeze_owned());
                if (req.retry_observation) observe('retry_observation', () => api.copy_observation());
            }
        }
        if (peer) out.peer_unchanged = out.peer_before === JSON.stringify(peer.snapshot());
        const snap = observe('final_snapshot', () => api.snapshot()); if (snap.ok) out.snapshot = snap.value;
    }
} finally {
    Object.freeze = freeze; global.Set = OriginalSet; Module._load = load; window.copy = copy;
    if (api) { out.settled = api.snapshot(); out.witness_cleared = api.witness === null; api.release(); api.release(); out.after_release = api.snapshot(); }
    if (peer) peer.release();
}
process.stdout.write(JSON.stringify(out));

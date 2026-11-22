'use strict';
// Temporary supplied data only; no compiler, target, producer or runtime issuer.
const fs = require('node:fs'), Module = require('node:module');
const req = JSON.parse(fs.readFileSync(0, 'utf8'));
const shared = require('../../core/compiler_stream_stager.cjs'), window = require('../../core/compiler_text_window.cjs');
const originalCopy = window.copy, load = Module._load;
let copies = 0, traps = 0, forbidden = 0, captured = null;
window.copy = function(...args) { copies++; captured = originalCopy(...args); return captured; };
Module._load = function(name, ...args) { if (!['./compiler_text_window.cjs', './compiler_preparation_capture.cjs', 'node:util'].includes(name)) { forbidden++; throw Error('forbidden'); } return load.call(this, name, ...args); };
const CUT = 'six_owner_shaped_supplied_synthetic_roots', VERSION = 'v1-private-same-capture-comparison';
const binding = { invocation_id: 'a'.repeat(64), tree_sha256: 'b'.repeat(64), source_sha256: 'c'.repeat(64), cut: CUT };
const caps = { version: VERSION, authority_kind: 'independent_declaration', invocation_id: binding.invocation_id,
    source_sha256: binding.source_sha256, cut: CUT, fuel: req.fuel ?? 1000, payload: req.payload ?? 10000000 };
if (req.cap_change) Object.assign(caps, req.cap_change);
const changed = { ...binding, [req.binding_field ?? 'invocation_id']: 'd'.repeat(64) };
let supplied = req.owner_input;
if (req.hook === 'input_getter') Object.defineProperty(supplied, 'intent', { enumerable: true, get() { traps++; throw Error('getter'); } });
if (req.hook === 'input_proxy') supplied = new Proxy(supplied, { ownKeys() { traps++; throw Error('proxy'); } });
if (req.hook === 'input_cycle') supplied.intent.cycle = supplied;
if (req.hook === 'caps_getter') Object.defineProperty(caps, 'fuel', { enumerable: true, get() { traps++; throw Error('getter'); } });
if (req.hook === 'caps_proxy') Object.setPrototypeOf(caps, null);
let owner = null, peer = null, quota = null, witness = null, api = null, bootstrapError = null;
let copiedFrozen = false, peerBefore = null, serialization = null;
const output = { constructed: false, bootstrap_error: null, native: { from: 0, compare: 0 }, events: [], snapshot: null,
    compiler_loaded: false, input_evaluated: false, compiler_or_target_operations: 0 };
const boot = req.boot;
try {
    owner = shared._captureSyntheticPreparationComparison(supplied, req.bad_capture_binding ? changed : binding);
    witness = owner.witness;
    copiedFrozen = Object.isFrozen(captured) && Object.isFrozen(captured.intent) && captured !== supplied;
    if (['foreign_witness_issue','foreign_quota_join'].includes(boot)) {
        peer = shared._captureSyntheticPreparationComparison(supplied, binding); peerBefore = JSON.stringify(peer.snapshot());
    }
    if (boot === 'missing_quota') owner.join(witness, null, binding);
    if (boot === 'close_before_issue') owner.close();
    quota = owner.issue_quota(boot === 'fake_witness_issue' ? {} : boot === 'foreign_witness_issue' ? peer.witness : witness,
        req.hook === 'caps_proxy' ? new Proxy(caps, { ownKeys() { traps++; throw Error('proxy'); } }) : caps,
        boot === 'stale_issue' ? changed : binding);
    if (boot === 'duplicate_issue') owner.issue_quota(witness, caps, binding);
    if (boot === 'close_before_join') owner.close();
    let quotaHandle = quota;
    if (boot === 'fake_quota_join') quotaHandle = {};
    if (boot === 'foreign_quota_join') { quotaHandle = peer.issue_quota(peer.witness, caps, binding); peerBefore = JSON.stringify(peer.snapshot()); }
    if (req.serialize) {
        serialization = {};
        for (const [name, value] of Object.entries({ owner, witness, quota })) {
            try { JSON.stringify(value); serialization[name] = false; } catch { serialization[name] = true; }
            try { structuredClone(value); serialization[name + '_clone'] = false; } catch { serialization[name + '_clone'] = true; }
        }
    }
    api = owner.join(boot === 'fake_witness_join' ? {} : witness, quotaHandle, boot === 'stale_join' ? changed : binding);
    if (boot === 'duplicate_join') owner.join(witness, quota, binding);
    if (req.mutate) { supplied.profiles.private_compiler_entry_context_handoff.z = 'changed'; caps.fuel = 999999; caps.payload = 99999999; }
} catch (e) { bootstrapError = { code: e.code ?? null, type: e.constructor.name }; }
output.constructed = api !== null; output.bootstrap_error = bootstrapError;
if (api && !bootstrapError) {
    const handle = api.context, from = Buffer.from, compare = Buffer.compare, freeze = Object.freeze;
    const original = new RangeError('unpersisted_native_detail'); let reentered = false, freezeFailures = 0;
    const observe = (site, fn) => { try { fn(); return { site, ok: true }; } catch (e) { return { site, ok: false, original_identity: e === original, code: e === original ? null : e.code ?? null }; } };
    if (req.ordinary) {
        const before = JSON.stringify(api.snapshot());
        output.ordinary = shared._canonicalCompilerStreamMetadata({z: 2, a: 1}, witness);
        shared._validateCompilerStreamValue(1, { const: 1 }, {}, 'ordinary_invalid', quota);
        output.ordinary_unchanged = before === JSON.stringify(api.snapshot());
    }
    Buffer.from = function(...args) {
        output.native.from++;
        if (req.reentry && !reentered) {
            reentered = true;
            output.reentry = observe('reentry', () => req.reentry === 'close' ? owner.close() : api.validate_root(handle, 'd', binding));
        }
        if (req.fault === 'first' && output.native.from === 1 || req.fault === 'second' && output.native.from === 2) throw original;
        return from(...args);
    };
    Buffer.compare = function(...args) { output.native.compare++; if (req.fault === 'compare') throw original; return compare(...args); };
    Object.freeze = function(value) { if (req.snapshot_fault && !freezeFailures && value?.scope === 'synthetic_comparison_completed_prefix') { freezeFailures++; throw original; } return freeze(value); };
    try {
        if (req.close_joined) owner.close();
        const sites = req.ordinary || req.close_joined ? [] : req.bad_site ? [req.bad_site]
            : ['d','t','i','a','i.expected_context','i.expected_context.entry_expected'];
        for (const site of sites) { const event = observe(site, () => api.validate_root(handle, site, binding)); output.events.push(event); if (!event.ok) break; }
        if (req.retry) { const before = JSON.stringify(api.snapshot()); output.retry = observe('retry', () => api.validate_root(handle, 'd', binding)); output.retry_unchanged = before === JSON.stringify(api.snapshot()); }
    } finally { Buffer.from = from; Buffer.compare = compare; Object.freeze = freeze; }
    output.freeze_failures = freezeFailures;
    output.snapshot = api.seal(req.loss ? false : true);
    output.idempotent_seal = JSON.stringify(api.seal()) === JSON.stringify(output.snapshot);
    if (req.issue_after) { const before = JSON.stringify(api.snapshot()); output.issue_after = observe('issue_after', () => owner.issue_quota(witness, caps, binding)); output.issue_after_unchanged = before === JSON.stringify(api.snapshot()); }
} else if (api) output.snapshot = api.snapshot();
if (owner) { owner.close(); output.owner_snapshot = owner.snapshot(); }
if (peer) { output.peer_unchanged = JSON.stringify(peer.snapshot()) === peerBefore; peer.close(); }
output.copy_calls = copies; output.copied_frozen = copiedFrozen; output.traps = traps; output.forbidden_calls = forbidden;
output.serialization = serialization;
process.stdout.write(JSON.stringify(output));

'use strict';
// Finite authored synthetic metadata; no actual owner/controller/target dispatch.
const fs = require('node:fs'), Module = require('node:module'), cp = require('node:child_process');
const req = JSON.parse(fs.readFileSync(0, 'utf8'));
const saved = { load: Module._load, freeze: Object.freeze, set: WeakMap.prototype.set,
    get: WeakMap.prototype.get, del: WeakMap.prototype.delete, from: Buffer.from, compare: Buffer.compare };
const out = { copies: 0, traps: 0, forbidden_calls: 0, faults: 0, events: [], native: { from: 0, compare: 0 },
    lease_returned: false, admitted: false, compiler_or_target_operations: 0, compiler_loaded: false, input_evaluated: false };
const original = req.error_kind === 'primitive' ? 'original_primitive' : req.error_kind === 'proxy'
    ? new Proxy({}, { get() { out.traps++; throw Error('inspected'); }, getPrototypeOf() { out.traps++; throw Error('inspected'); } })
    : new RangeError('original_native');
let owner, stager, window;
for (const name of req.load_order === 'stager' ? ['stager','window','owner'] : req.load_order === 'window'
    ? ['window','owner','stager'] : ['owner','window','stager']) {
    if (name === 'owner') owner = require('../../core/compiler_preparation_capture.cjs');
    if (name === 'stager') stager = require('../../core/compiler_stream_stager.cjs');
    if (name === 'window') window = require('../../core/compiler_text_window.cjs');
}
if (!req.skip_init) stager._initializePhasedPreparationComparisonCapture();
const copy = window.copy, CUT = 'one_live_phased_copy_six_preparation_roots';
const binding = { invocation_id: 'a'.repeat(64), tree_sha256: 'b'.repeat(64), source_sha256: 'c'.repeat(64), cut: CUT, ...req.binding_change };
const caps = { version: 'v1-private-live-phased-comparison', authority_kind: 'independent_declaration',
    invocation_id: binding.invocation_id, source_sha256: binding.source_sha256, cut: CUT,
    fuel: req.fuel ?? 1000, payload: req.payload ?? 10000000, ...req.cap_change };
let attempt = null, lease = null, comparator = null, exposedLease = null, exposedContext = null, held = null,
    captured = null, receipt = null, witness = null, contextState = null, contextMap = null, contextToken = null,
    registrationCore = null, refusedError = null, pending = null, beforeRelease = false;
const code = error => stager._preparationComparisonFailureCode(error) || owner._preparationCaptureFailureCode(error);
const observe = (site, fn) => {
    try { return { ok: true, value: fn() }; }
    catch (error) {
        refusedError = error;
        const owned = stager._preparationComparisonFailureCode(error);
        out.events.push({ site, ok: false, original_identity: error === original, code: owned || owner._preparationCaptureFailureCode(error), comparison_owned: owned !== null });
        return { ok: false };
    }
};
const run = (site, fn) => { const e = observe(site,fn); if (e.ok) out.events.push({ site, ok: true }); return e; };
Module._load = () => { out.forbidden_calls++; throw Error('late_load'); };
const io = [];
for (const [o, names] of [[fs,['openSync','readFileSync','readSync','statSync','lstatSync','realpathSync','writeFileSync']],
    [cp,['spawn','spawnSync','exec','execSync','execFile','execFileSync','fork']]]) for (const n of names) {
    io.push([o,n,o[n]]); o[n] = () => { out.forbidden_calls++; throw Error('unowned_io'); };
}
window.copy = function(...args) { out.copies++; captured = copy(...args); return captured; };
WeakMap.prototype.set = function(k, v) {
    if (v && Object.hasOwn(v,'core') && Object.hasOwn(v,'spent')) {
        registrationCore = v.core;
        if (req.release_fault) {
            const release = v.core.release;
            v.core.release = function(...args) { release.apply(this,args); if (!out.faults) { out.faults++; throw original; } };
        }
    }
    if (v && Object.hasOwn(v,'stats') && Object.hasOwn(v,'held')) {
        contextState = v; contextMap = this; contextToken = k;
        const h = captured, r = v.held;
        out.same_roots = r.d.value === h.profiles.private_compiler_entry_context_handoff && r.t.value === h.profiles.private_compiler_entry_lifetime_trace
            && r.i.value === h.intent && r.a.value === h.anchors && r['i.expected_context'].value === h.intent.expected_context
            && r['i.expected_context.entry_expected'].value === h.intent.expected_context.entry_expected && r.d.schema === h.schemas.handoff;
        if (req.fault === 'context_registration' && !out.faults) { saved.set.call(this,k,v); out.faults++; throw original; }
    }
    if (v && Object.hasOwn(v,'ready') && Object.hasOwn(v,'code')) {
        out.registration_lookup_before_ready = stager._preparationComparisonFailureCode(k);
        if (req.refusal_registration && !out.faults) {
            if (req.refusal_registration === 'after') saved.set.call(this,k,v);
            out.unpublished_refusal = { code: stager._preparationComparisonFailureCode(k), retained: k };
            out.faults++; throw original;
        }
    }
    return saved.set.call(this,k,v);
};
WeakMap.prototype.delete = function(k) {
    if (req.cleanup_fault && this === contextMap) { out.cleanup_faults = (out.cleanup_faults || 0) + 1; throw 'secondary_cleanup'; }
    return saved.del.call(this,k);
};
Object.freeze = function(v) {
    const stage = v === captured ? 'tree' : v && Object.hasOwn(v,'outcome') && Object.hasOwn(v,'nodes') ? 'receipt'
        : v?.held === captured && Object.hasOwn(v,'witness') ? 'view'
        : typeof v?.borrow === 'function' && typeof v?.admit === 'function' ? 'lease_api'
        : typeof v?.validate_root === 'function' ? 'context_api' : v?.local_copy_witness_verified ? 'link'
        : beforeRelease && v?.version && v?.counters ? 'cleanup_snapshot' : null;
    if (stage === 'view') { out.same_receipt = v.copy_charges === receipt; out.same_witness = v.witness === witness; }
    if (stage === 'lease_api') {
        exposedLease = v;
        if (req.lease_publication) run('lease_reentry', () => req.lease_publication === 'borrow' ? v.borrow()
            : req.lease_publication === 'admit' ? v.admit(caps,binding) : req.lease_publication === 'release' ? v.release() : v.snapshot());
    }
    if (stage === 'context_api') {
        exposedContext = v; out.context_unpublished = v.context === null;
        if (req.admission_reentry) run('admission_reentry', () => req.admission_reentry === 'release' ? lease.release()
            : req.admission_reentry === 'borrow' ? lease.borrow() : lease.admit(caps,binding));
    }
    if (stage === 'link' && req.link_reentry) run('link_reentry', () => lease.release());
    if (req.fault === stage && !out.faults) { out.faults++; throw original; }
    return saved.freeze(v);
};
Buffer.from = function(...args) {
    out.native.from++;
    if (req.native_fault === 'first' && out.native.from === 1 || req.native_fault === 'second' && out.native.from === 2) throw original;
    const b = saved.from(...args);
    if (req.native_release && out.native.from === 1) run('native_release', () => lease.release());
    return b;
};
Buffer.compare = function(...args) { out.native.compare++; if (req.native_fault === 'compare') throw original; return saved.compare(...args); };
try {
    if (req.lookup_only) {
        const hostile = new Proxy({}, { get() { out.traps++; throw Error('inspected'); }, getPrototypeOf() { out.traps++; throw Error('inspected'); } });
        const getter = Object.create(null, { code: { get() { out.traps++; throw Error('inspected'); } } });
        out.unknown_codes = [null,undefined,7,'comparison_budget',true,hostile,getter,new Error('comparison_budget'),{ code:'comparison_budget' }]
            .map(x => stager._preparationComparisonFailureCode(x));
    } else {
        attempt = owner._createPreparationCopyAttempt(); attempt.copy_input(req.owner_input);
        if (req.state !== 'no_receipt') receipt = run('receipt', () => attempt.copy_observation()).value;
        if (req.outward_gap && receipt) run('outward_gap', () => { throw original; });
        if (req.state !== 'not_frozen') attempt.freeze_owned();
        witness = attempt.witness;
        if (req.state === 'borrowed') attempt.take_for_context_owner(witness);
        if (req.state === 'released') attempt.release();
        let a = attempt, w = witness;
        if (req.bad_attempt === 'fake') a = { get release() { out.traps++; throw Error('fake_release'); } };
        if (req.bad_attempt === 'proxy') a = new Proxy({}, { get() { out.traps++; throw Error('inspected'); }, getPrototypeOf() { out.traps++; throw Error('inspected'); } });
        if (req.bad_attempt === 'clone') a = { ...attempt };
        if (req.bad_witness) w = {};
        const args = [a,w]; if (req.prepare_extra) args.push(() => { out.traps++; });
        lease = run('prepare', () => stager._preparePhasedComparisonOwnerLease(...args)).value;
        out.original_after_transfer = attempt.snapshot();
        if (lease) {
            out.lease_returned = true; out.prepared = lease.snapshot();
            if (req.repeat_prepare) run('repeat_prepare', () => stager._preparePhasedComparisonOwnerLease(attempt,witness));
            if (req.close_before_borrow) lease.release();
            if (!req.skip_borrow) {
                const args = req.borrow_extra ? [() => { out.traps++; }] : [];
                held = run('borrow', () => lease.borrow(...args)).value;
                if (held) {
                    out.same_held = held === captured; out.frozen = Object.isFrozen(held) && Object.isFrozen(held.intent);
                    out.borrowed = lease.snapshot();
                    // A source-grounded owner prefix reads its frozen schema,
                    // not a real policy/schema validator or a second source.
                    out.prefix_uses_same_schema = held.schemas.handoff === captured.schemas.handoff;
                    if (req.mutate) { req.owner_input.intent.z = 'mutated'; out.borrow_stable = held.intent.z[0] === 1; }
                    if (req.repeat_borrow) run('repeat_borrow', () => lease.borrow());
                }
            }
            if (req.close_before_admit) lease.release();
            if (req.cap_hook) Object.defineProperty(caps,'fuel',{ enumerable:true,get() { out.traps++; throw Error('getter'); } });
            let b = binding;
            if (req.binding_proxy) b = new Proxy(binding,{ get() { out.traps++; throw Error('inspected'); }, getPrototypeOf() { out.traps++; throw Error('inspected'); } });
            const args = [caps,b]; if (req.admit_extra) args.push(() => { out.traps++; });
            comparator = run('admit', () => lease.admit(...args)).value;
            out.after_admit = lease.snapshot();
            if (req.retry_admit) run('retry_admit', () => lease.admit(caps,binding));
            if (comparator) {
                out.admitted = true;
                if (req.mutate) caps.fuel = 100000;
                const handle = comparator.context, current = { ...binding };
                if (req.current_field) current[req.current_field] = 'd'.repeat(64);
                const names = ['d','t','i','a','i.expected_context','i.expected_context.entry_expected'];
                for (const site of req.bad_site ? [req.bad_site] : names.slice(0,req.root_count ?? 6)) {
                    const e = run(site, () => comparator.validate_root(handle,site,current)); if (!e.ok) break;
                }
                pending = refusedError;
                if (req.mutate_error_code && pending) { pending.code = 'forged'; out.lookup_after_code_mutation = code(pending); }
                beforeRelease = true; run('release', () => lease.release()); beforeRelease = false;
                out.comparison = comparator.snapshot();
                if (pending) out.primary_still_owned = stager._preparationComparisonFailureCode(pending);
                out.closed_handle_null = comparator.context === null;
                run('late_root', () => comparator.validate_root(handle,'d',binding));
            }
            run('final_release', () => lease.release());
            out.closed = lease.snapshot();
            run('closed_borrow', () => lease.borrow()); run('closed_admit', () => lease.admit(caps,binding));
        }
        if (exposedLease && !lease) out.unreturned_lease_snapshot_refused = !run('unreturned_lease', () => exposedLease.snapshot()).ok;
        if (exposedContext && !comparator) {
            out.unreturned_context_null = exposedContext.context === null;
            const e = run('unreturned_context', () => exposedContext.snapshot());
            if (e.ok) out.unreturned_context = e.value;
        }
        if (contextState) out.context_rows_cleared = contextState.held === null;
        if (contextMap) out.context_token_removed = !saved.get.call(contextMap,contextToken);
        if (registrationCore) out.original_core_cleared = registrationCore.references_cleared;
    }
    if (out.unpublished_refusal) {
        out.unpublished_refusal_after_failure = stager._preparationComparisonFailureCode(out.unpublished_refusal.retained);
        delete out.unpublished_refusal.retained;
    }
} finally {
    Module._load = saved.load; Object.freeze = saved.freeze; WeakMap.prototype.set = saved.set; WeakMap.prototype.delete = saved.del;
    Buffer.from = saved.from; Buffer.compare = saved.compare; window.copy = copy;
    for (const [o,n,f] of io) o[n] = f;
    held = captured = receipt = witness = null;
    if (lease) lease.release(); if (attempt) { attempt.release(); out.original_after_cleanup = attempt.snapshot(); }
}
process.stdout.write(JSON.stringify(out));

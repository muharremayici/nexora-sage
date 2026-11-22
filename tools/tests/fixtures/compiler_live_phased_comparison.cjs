'use strict';
// Authored synthetic metadata only. Host hooks observe identity/failure, not authority.
const fs = require('node:fs'), Module = require('node:module'), cp = require('node:child_process');
const req = JSON.parse(fs.readFileSync(0, 'utf8'));
const load = Module._load, freeze = Object.freeze, set = WeakMap.prototype.set, del = WeakMap.prototype.delete,
    get = WeakMap.prototype.get, from = Buffer.from, compare = Buffer.compare;
const out = { copies: 0, traps: 0, forbidden_calls: 0, faults: 0, events: [], native: { from: 0, compare: 0 },
    constructed: false, snapshot: null, compiler_loaded: false, input_evaluated: false, compiler_or_target_operations: 0 };
const original = req.error_kind === 'primitive' ? 'synthetic_primitive'
    : req.error_kind === 'proxy' ? new Proxy({}, { get() { out.traps++; throw Error('inspected'); },
        getPrototypeOf() { out.traps++; throw Error('inspected'); } }) : new RangeError('synthetic_failure');
let owner, stager, window, attempt = null, peer = null, context = null, captured = null, witness = null,
    exposed = null, inJoin = false, contextMap = null, contextToken = null, contextState = null, registrationCore = null;
const event = (site, fn) => {
    try { return { site, ok: true, value: fn() }; }
    catch (error) { return { site, ok: false, original_identity: error === original,
        code: error === original ? null : owner?._preparationCaptureFailureCode(error) || error?.code || null }; }
};
const observe = (site, fn) => { const e = event(site, fn); const value = e.value; delete e.value; out.events.push(e); return { e, value }; };
for (const name of req.load_order === 'stager' ? ['stager','window','owner'] : req.load_order === 'window' ? ['window','owner','stager'] : ['owner','window','stager']) {
    if (name === 'owner') owner = require('../../core/compiler_preparation_capture.cjs');
    if (name === 'stager') stager = require('../../core/compiler_stream_stager.cjs');
    if (name === 'window') window = require('../../core/compiler_text_window.cjs');
}
const copy = window.copy;
const CUT = 'one_live_phased_copy_six_preparation_roots', VERSION = 'v1-private-live-phased-comparison';
const binding = { invocation_id: 'a'.repeat(64), tree_sha256: 'b'.repeat(64), source_sha256: 'c'.repeat(64), cut: CUT };
const caps = { version: VERSION, authority_kind: 'independent_declaration', invocation_id: binding.invocation_id,
    source_sha256: binding.source_sha256, cut: CUT, fuel: req.fuel ?? 1000, payload: req.payload ?? 10000000, ...req.cap_change };
if (req.binding_change) Object.assign(binding, req.binding_change);
let setupLoads = 0, setupReentered = false;
Module._load = function(name, ...args) {
    if (name === './compiler_preparation_capture.cjs') {
        setupLoads++;
        if (req.init_fault) { out.faults++; throw original; }
        if (req.init_reentry && !setupReentered) { setupReentered = true; observe('init_reentry', () => stager._initializePhasedPreparationComparisonCapture()); }
    }
    return load.call(this, name, ...args);
};
if (!req.skip_init) observe('initialize', () => req.init_extra
    ? stager._initializePhasedPreparationComparisonCapture(() => { out.traps++; })
    : stager._initializePhasedPreparationComparisonCapture());
if (req.init_fault || req.init_reentry) observe('init_retry', () => stager._initializePhasedPreparationComparisonCapture());
out.setup_loads = setupLoads;
Module._load = function(name, ...args) {
    // Legacy constructors retain their original fixed lazy module calls. The
    // new phased path has no exception, including cached requires.
    if (req.mode === 'old' && name === './compiler_preparation_capture.cjs'
        || req.mode === 'raw' && name === './compiler_text_window.cjs') return load.call(this,name,...args);
    out.forbidden_calls++; throw Error('late_load');
};
const io = [];
for (const [obj, names] of [[fs,['openSync','readFileSync','readSync','statSync','lstatSync','realpathSync','writeFileSync']],
                           [cp,['spawn','spawnSync','exec','execSync','execFile','execFileSync','fork']]]) {
    for (const name of names) { io.push([obj,name,obj[name]]); obj[name] = () => { out.forbidden_calls++; throw Error('unowned_io'); }; }
}
window.copy = function(...args) { out.copies++; captured = copy(...args); return captured; };
WeakMap.prototype.set = function(key, value) {
    if (value && Object.hasOwn(value, 'core') && Object.hasOwn(value, 'spent')) {
        registrationCore = value.core;
        if (req.release_fault) {
            const release = registrationCore.release;
            registrationCore.release = function(...args) {
                release.apply(this,args);
                if (contextState && out.faults === 0) { out.faults++; throw original; }
            };
        }
        if (req.registration_release) key.release();
        if (req.fault === 'attempt_registration' && out.faults === 0) { set.call(this,key,value); out.faults++; throw original; }
    }
    if (value && Object.hasOwn(value, 'stats') && Object.hasOwn(value, 'held')) {
        contextMap = this; contextToken = key; contextState = value;
        if (req.mode !== 'raw') {
            const rows = value.held, h = captured;
            out.same_root_references = rows.d.value === h.profiles.private_compiler_entry_context_handoff
                && rows.t.value === h.profiles.private_compiler_entry_lifetime_trace && rows.i.value === h.intent
                && rows.a.value === h.anchors && rows['i.expected_context'].value === h.intent.expected_context
                && rows['i.expected_context.entry_expected'].value === h.intent.expected_context.entry_expected
                && rows.d.schema === h.schemas.handoff;
        }
        if (req.fault === 'context_registration' && out.faults === 0) { set.call(this,key,value); out.faults++; throw original; }
    }
    return set.call(this,key,value);
};
WeakMap.prototype.delete = function(key) {
    if (req.cleanup_fault && this === contextMap) { out.cleanup_faults = (out.cleanup_faults || 0) + 1; throw 'secondary_cleanup'; }
    return del.call(this,key);
};
Object.freeze = function(value) {
    const stage = value === captured ? 'tree'
        : value && Object.hasOwn(value,'outcome') && Object.hasOwn(value,'nodes') ? 'observation'
        : value?.held === captured && Object.hasOwn(value,'witness') ? 'view'
        : typeof value?.copy_input === 'function' ? 'attempt_api'
        : typeof value?.validate_root === 'function' ? 'context_api'
        : value?.local_copy_witness_verified ? 'link'
        : inJoin && typeof value?.toJSON === 'function' ? 'context_token' : null;
    if (stage === 'view') {
        out.same_receipt = value.copy_charges === out.receipt;
        out.same_witness = value.witness === witness;
        if (req.view_action === 'release') attempt.release();
        if (req.view_action === 'reenter' && !out.view_reentered) {
            out.view_reentered = true; observe('view_reentry', () => owner._takePhasedPreparationForComparison(attempt,witness));
        }
    }
    if (stage === 'context_api') {
        exposed = value; out.unpublished_context_null = value.context === null;
        if (req.publication_action) observe('publication_reentry', () => req.publication_action === 'validate'
            ? value.validate_root(value.context,'d',binding) : req.publication_action === 'seal' ? value.seal() : value.snapshot());
    }
    if (req.fault === stage && out.faults === 0) { out.faults++; throw original; }
    return freeze(value);
};
Buffer.from = function(...args) {
    out.native.from++;
    if (req.native_fault === 'first' && out.native.from === 1 || req.native_fault === 'second' && out.native.from === 2) throw original;
    const result = from(...args);
    if (req.native_reentry && out.native.from === 1) observe('native_reentry', () => context.validate_root(context.context,'d',binding));
    return result;
};
Buffer.compare = function(...args) { out.native.compare++; if (req.native_fault === 'compare') throw original; return compare(...args); };
try {
    if (req.repeat_init) observe('initialize_again', () => stager._initializePhasedPreparationComparisonCapture());
    if (req.mode === 'old' || req.mode === 'raw' || req.mode === 'capture') {
        const cut = req.mode !== 'raw' ? 'six_owner_shaped_supplied_synthetic_roots' : 'six_supplied_synthetic_roots';
        const b = { ...binding, cut };
        if (req.digest_suffix) b[req.digest_field || 'source_sha256'] += req.digest_suffix;
        const c = { ...caps, version: req.mode === 'old' ? 'v1-private-same-capture-comparison' : 'v1-private-supplied-synthetic-comparison-budget', cut,
            source_sha256: b.source_sha256, invocation_id: b.invocation_id };
        const built = observe('join', () => {
            inJoin = true;
            if (req.mode === 'capture') return owner._capturePreparation(req.owner_input,b);
            if (req.mode === 'raw') return stager._createSyntheticPreparationComparison(req.rows,c,b);
            peer = stager._captureSyntheticPreparationComparison(req.owner_input,b);
            return peer.join(peer.witness,peer.issue_quota(peer.witness,c,b),b);
        }); context = built.value; inJoin = false;
    } else if (!req.only_init) {
        const built = observe('attempt', () => owner._createPreparationCopyAttempt()); attempt = built.value;
        if (attempt) {
            const copied = observe('copy', () => attempt.copy_input(req.owner_input));
            if (copied.e.ok) {
                if (req.state !== 'no_observation') out.receipt = observe('observation', () => attempt.copy_observation()).value;
                if (req.state !== 'not_frozen') observe('freeze', () => attempt.freeze_owned());
                witness = attempt.witness;
            }
            if (req.state === 'released') attempt.release();
            if (req.state === 'borrowed') observe('old_borrow', () => attempt.take_for_context_owner(witness));
            let a = attempt, w = witness;
            if (req.bad_attempt === 'fake') a = { get release() { out.traps++; throw Error('fake_release'); } };
            if (req.bad_attempt === 'proxy') a = new Proxy({}, { get() { out.traps++; throw Error('fake'); }, getPrototypeOf() { out.traps++; throw Error('fake'); } });
            if (req.bad_attempt === 'clone') a = { ...attempt };
            if (req.bad_witness === 'fake') w = {};
            if (req.bad_witness === 'foreign') {
                peer = owner._createPreparationCopyAttempt(); peer.copy_input(req.owner_input); peer.copy_observation(); peer.freeze_owned(); w = peer.witness;
                out.peer_before = JSON.stringify(peer.snapshot());
            }
            if (req.cap_hook) Object.defineProperty(caps,'fuel',{ enumerable:true,get() { out.traps++; throw Error('caps_getter'); } });
            if (req.binding_proxy) Object.setPrototypeOf(binding,Date.prototype);
            if (req.digest_suffix) binding[req.digest_field || 'source_sha256'] += req.digest_suffix;
            const args = [a,w,caps,binding]; if (req.extra) args.push(() => { out.traps++; });
            const joined = observe('join', () => { inJoin = true; return stager._joinPhasedPreparationComparison(...args); });
            inJoin = false; context = joined.value;
            if (req.repeat_join && context) observe('repeat_join', () => stager._joinPhasedPreparationComparison(attempt,witness,caps,binding));
            out.before_cleanup = attempt.snapshot();
            if (req.bad_witness === 'foreign') out.peer_unchanged = out.peer_before === JSON.stringify(peer.snapshot());
        }
    }
    delete out.receipt;
    if (context) {
        out.constructed = true;
        if (req.mutate) { caps.fuel = 1000000; req.owner_input.intent.z = 'changed'; }
        const current = { ...binding }; if (req.current_field) current[req.current_field] = 'd'.repeat(64);
        const handle = context.context;
        const names = ['d','t','i','a','i.expected_context','i.expected_context.entry_expected'];
        for (const site of req.bad_site ? [req.bad_site] : names) {
            const e = observe(site, () => context.validate_root(handle,site,current)); if (!e.e.ok) break;
        }
        out.snapshot = context.seal(); out.idempotent_seal = JSON.stringify(out.snapshot) === JSON.stringify(context.seal());
    }
    if (exposed && !context) {
        out.failed_publication_context_null = exposed.context === null;
        const observed = observe('unreturned_snapshot', () => exposed.snapshot());
        if (observed.e.ok) out.unreturned_observation = observed.value;
    }
    if (contextState) out.constructor_rows_cleared = contextState.held === null;
    if (contextMap) out.constructor_token_removed = !get.call(contextMap,contextToken);
    if (registrationCore) out.registration_core_cleared = registrationCore.references_cleared;
} finally {
    Object.freeze = freeze; WeakMap.prototype.set = set; WeakMap.prototype.delete = del; Buffer.from = from; Buffer.compare = compare;
    window.copy = copy; Module._load = load; for (const [o,n,f] of io) o[n] = f;
    if (attempt) { attempt.release(); out.after_cleanup = attempt.snapshot(); }
    if (peer) { if (peer.release) peer.release(); else peer.close(); }
}
process.stdout.write(JSON.stringify(out));

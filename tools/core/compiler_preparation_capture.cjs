'use strict';
// Disabled, supplied-synthetic preparation only. Local ownership is not authority.
const { types } = require('node:util');
const preparationCopier = require('./compiler_text_window.cjs');
const failures = new WeakMap();
// Genuine published phased APIs only; a spent entry stays visible during a
// transfer so caught reentry closes the same core rather than reviving it.
const phasedAttempts = new WeakMap();
class PreparationCaptureRefusal extends Error {
    constructor(code) { super(code); this.code = code; failures.set(this, code); }
}
const need = (ok, code) => { if (!ok) throw new PreparationCaptureRefusal(code); };
// Do not inspect an arbitrary thrown object, including proxies and primitives.
const failureCode = error => failures.get(error) || null;
const VERSION = 'v1-private-source-owned-preparation-capture';
const SCOPE = 'six_owner_shaped_supplied_synthetic_roots';
const KEYS = ['invocation_id', 'tree_sha256', 'source_sha256', 'cut'];
function record(value, keys) {
    need(value && typeof value === 'object' && !types.isProxy(value)
        && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null), 'comparison_metadata');
    const ds = Object.getOwnPropertyDescriptors(value), names = Reflect.ownKeys(ds);
    need(names.length === keys.length && names.every(k => typeof k === 'string'
        && keys.includes(k) && Object.hasOwn(ds[k], 'value')), 'comparison_metadata');
    return Object.fromEntries(keys.map(k => [k, ds[k].value]));
}

// This internal lifecycle is never handed out raw. Each fixed facade publishes
// its own immutable API and chooses its existing observation/freeze order.
function newCopyAttempt() {
    let held = null, witness = null, budget = null, receipt = null, phase = 'NEW', ready = false;
    let active = false, lifetime = {}, calls = 0, outcome = 'not_started', observed = 'not_requested';
    let borrowed = false, frozen = false;
    const clear = () => { held = witness = budget = null; phase = 'RELEASED'; lifetime = {}; };
    const reject = error => { clear(); throw error; };
    const enter = () => { need(ready && !active, 'preparation_capture_reentry'); active = true; return lifetime; };
    const live = ticket => need(lifetime === ticket, 'preparation_capture_closed');
    const operate = action => {
        let entered = false;
        try { const ticket = enter(); entered = true; const result = action(ticket); live(ticket); return result; }
        catch (error) { return reject(error); }
        finally { if (entered) active = false; }
    };
    const core = {
        activate() { need(!ready && phase === 'NEW', 'preparation_capture_closed'); ready = true; },
        assert_publication() { need(ready && !active && phase === 'NEW', 'preparation_capture_closed'); },
        get references_cleared() { return held === null; },
        get copy_outcome() { return outcome; },
        copy_input(input) {
            let ticket, entered = false, started = false, returned = false;
            try {
                ticket = enter(); entered = true;
                need(arguments.length === 1, 'preparation_capture_arguments');
                need(phase === 'NEW', 'preparation_capture_copy_state');
                const capture = preparationCopier.copy;
                live(ticket);
                budget = { nodes: 0, bytes: 0 }; const seen = new Set(); live(ticket);
                phase = 'COPYING'; calls = 1; outcome = 'threw'; started = true;
                const copied = capture(input, 'comparison_capture_copy', 65536, seen, 0, budget);
                outcome = 'returned'; returned = true; live(ticket);
                held = copied;
                record(held, ['intent', 'anchors', 'profiles', 'schemas']);
                need(Number.isSafeInteger(budget.nodes) && budget.nodes >= 1 && budget.nodes <= 65536
                    && Number.isSafeInteger(budget.bytes) && budget.bytes >= 0 && budget.bytes <= 65536, 'comparison_arithmetic');
                live(ticket); phase = 'COPY_RETURNED';
            } catch (error) {
                if (entered && lifetime === ticket && started && !returned) { held = null; phase = 'COPY_THREW'; }
                else clear();
                throw error;
            } finally { input = null; if (entered) active = false; }
        },
        copy_observation() {
            let ticket, entered = false, emission = false;
            try {
                ticket = enter(); entered = true;
                need(arguments.length === 0, 'preparation_capture_arguments');
                need(['COPY_RETURNED', 'COPY_THREW', 'FROZEN'].includes(phase)
                    && observed === 'not_requested', 'preparation_capture_observation_state');
                // Spend before allocation; a failed emission cannot be retried.
                observed = 'unavailable'; emission = true;
                need(budget !== null && Number.isSafeInteger(budget.nodes) && budget.nodes >= 0
                    && Number.isSafeInteger(budget.bytes) && budget.bytes >= 0, 'comparison_arithmetic');
                // Partial failed charges may exceed admission ceilings. Never clamp them.
                const measured = Object.freeze({ outcome, nodes: budget.nodes, scalar_key_charges: budget.bytes,
                    ceiling: 65536, starting_depth: 0, depth_ceiling: 32 });
                live(ticket); receipt = measured; observed = 'acquired'; return receipt;
            } catch (error) {
                // Observation allocation failure alone does not discard a returned tree.
                if (!entered || !emission || lifetime !== ticket) clear();
                throw error;
            } finally { if (emission) budget = null; if (entered) active = false; }
        },
        freeze_owned() {
            return operate(ticket => {
                need(arguments.length === 0, 'preparation_capture_arguments');
                need(phase === 'COPY_RETURNED', 'preparation_capture_freeze_state');
                const freeze = value => {
                    live(ticket);
                    if (value && typeof value === 'object') {
                        const children = Object.values(value); live(ticket);
                        for (const child of children) freeze(child);
                        Object.freeze(value); live(ticket);
                    }
                };
                freeze(held);
                need(phase === 'COPY_RETURNED' && held !== null, 'preparation_capture_closed');
                frozen = true; phase = 'FROZEN';
            });
        },
        get witness() {
            if (!ready || phase !== 'FROZEN') return null;
            return operate(() => {
                if (witness === null) {
                    const token = Object.freeze({ toJSON() { throw new TypeError('private_preparation_witness_not_serializable'); },
                        [Symbol.for('nodejs.util.inspect.custom')]() { return '<private preparation copy witness>'; } });
                    need(phase === 'FROZEN' && held !== null, 'preparation_capture_closed');
                    witness = token;
                }
                return witness;
            });
        },
        take_for_context_owner(handle) {
            return operate(() => {
                need(arguments.length === 1, 'preparation_capture_arguments');
                need(phase === 'FROZEN', 'preparation_capture_closed');
                need(witness !== null && handle === witness, 'preparation_capture_witness');
                phase = 'BORROWED'; borrowed = true;
                return Object.freeze({ held, witness, copy_charges: receipt });
            });
        },
        take_for_comparison_owner(handle) {
            return operate(() => {
                need(phase === 'FROZEN', 'preparation_capture_closed');
                need(witness !== null && handle === witness, 'preparation_capture_witness');
                need(outcome === 'returned' && observed === 'acquired' && receipt !== null,
                    'preparation_capture_observation');
                phase = 'BORROWED'; borrowed = true;
                return Object.freeze({ held, witness, copy_charges: receipt });
            });
        },
        snapshot() {
            return operate(() => {
                need(arguments.length === 0, 'preparation_capture_arguments');
                return Object.freeze({ version: 'v1-private-phased-preparation-copy-attempt',
                    scope: 'one_fixed_owner_shaped_supplied_copy_attempt', phase, copy_calls: calls, copy_outcome: outcome,
                    copy_observation_status: observed, copy_charges: receipt,
                    copy_observation_gap: observed === 'acquired' ? null : observed === 'unavailable' ? 'snapshot_unavailable' : 'not_requested',
                    references_cleared: held === null, borrowed_once: borrowed, frozen_once: frozen,
                    input_origin_authenticated: false, source_identity_verified: false, budget_authority_verified: false,
                    current_owner_enforcement: false, runtime_authority_available: false, production_enabled: false,
                    semantic_lineage: false, cache_eligible: false, publication_authority: false,
                    complete_cost_priced: false, full_work_units: null, physical_fit: null });
            });
        },
        release() { try { need(arguments.length === 0, 'preparation_capture_arguments'); } finally { clear(); } }
    };
    return core;
}

function createPreparationCopyAttempt() {
    need(arguments.length === 0, 'preparation_capture_arguments');
    const core = newCopyAttempt();
    let api = null;
    try {
        api = Object.freeze({
            copy_input(input) { return core.copy_input(...arguments); },
            copy_observation() { return core.copy_observation(...arguments); },
            freeze_owned() { return core.freeze_owned(...arguments); },
            get witness() { return core.witness; },
            get copy_outcome() { return core.copy_outcome; },
            take_for_context_owner(handle) { return core.take_for_context_owner(...arguments); },
            snapshot() { return core.snapshot(...arguments); }, release() { return core.release(...arguments); },
            toJSON() { throw new TypeError('private_preparation_copy_attempt_not_serializable'); },
            [Symbol.for('nodejs.util.inspect.custom')]() { return '<private preparation copy attempt>'; }
        });
        core.activate();
        phasedAttempts.set(api, { core, spent: false });
        core.assert_publication();
        return api;
    } catch (error) {
        try { if (api !== null) phasedAttempts.delete(api); } catch { /* Keep primary. */ }
        try { core.release(); } catch { /* Keep primary. */ }
        throw error;
    }
}

function takePhasedPreparationForComparison(attempt, handle) {
    // Weak identity lookup does not inspect a forged object's methods/getters.
    const entry = phasedAttempts.get(attempt);
    need(entry !== undefined, 'preparation_capture_attempt');
    let failed = false;
    try {
        need(!entry.spent, 'preparation_capture_closed');
        entry.spent = true; // Spend before the view allocation, never refund.
        need(arguments.length === 2, 'preparation_capture_arguments');
        return entry.core.take_for_comparison_owner(handle);
    } catch (error) {
        failed = true;
        try { entry.core.release(); } catch { /* Keep primary. */ }
        throw error;
    } finally {
        try { phasedAttempts.delete(attempt); }
        catch (error) {
            if (!failed) {
                try { entry.core.release(); } catch { /* Keep primary. */ }
                throw error;
            }
        }
        attempt = handle = null;
    }
}

function capturePreparation(input, bindingInput) {
    let core = null, witness = null, phase = 'CONSTRUCTING', active = false, borrowed = false, lifetime = {};
    const clear = () => { if (core !== null) core.release(); witness = null; phase = 'RELEASED'; lifetime = {}; };
    const reject = error => { clear(); throw error; };
    try {
        need(arguments.length === 2, 'preparation_capture_arguments');
        const binding = record(bindingInput, KEYS);
        need(KEYS.slice(0, 3).every(k => typeof binding[k] === 'string' && binding[k].length === 64 && /^[a-f0-9]{64}$/.test(binding[k]))
            && binding.cut === SCOPE, 'comparison_binding');
        core = newCopyAttempt(); core.activate(); core.copy_input(input);
        core.freeze_owned(); // Preserve this facade's existing freeze-before-receipt order.
        const copyReceipt = core.copy_observation();
        witness = core.witness;
        input = bindingInput = null;
        const operate = (action, allowReleased = false) => {
            try {
                need(!active && phase !== 'CONSTRUCTING', 'preparation_capture_reentry');
                need(allowReleased || phase !== 'RELEASED', 'preparation_capture_closed');
                const operationLifetime = lifetime;
                active = true;
                const result = action();
                // A caught release/reentry during metadata/view allocation cannot return success.
                need(lifetime === operationLifetime, 'preparation_capture_closed');
                return result;
            } catch (error) { return reject(error); }
            finally { active = false; }
        };
        const api = {
            get witness() { return phase === 'CAPTURED' ? witness : null; },
            take_for_comparison(handle, currentBinding) {
                return operate(() => {
                    need(arguments.length === 2, 'preparation_capture_arguments');
                    need(phase === 'CAPTURED', 'preparation_capture_closed');
                    need(handle === witness, 'preparation_capture_witness');
                    const current = record(currentBinding, KEYS);
                    need(KEYS.every(k => current[k] === binding[k]), 'comparison_binding');
                    need(phase === 'CAPTURED' && !core.references_cleared && witness === handle, 'preparation_capture_closed');
                    phase = 'BORROWED'; borrowed = true; // Spend before the view allocation.
                    return core.take_for_context_owner(handle);
                });
            },
            snapshot() {
                // Closed snapshots contain only the acquired copy receipt, never the tree.
                return operate(() => {
                    need(arguments.length === 0, 'preparation_capture_arguments');
                    return Object.freeze({ version: VERSION, scope: SCOPE, phase, copy_charges: copyReceipt,
                        references_cleared: core.references_cleared, borrowed_once: borrowed, input_origin_authenticated: false,
                        source_identity_verified: false, budget_authority_verified: false, runtime_authority_available: false });
                }, true);
            },
            release() {
                try { need(arguments.length === 0, 'preparation_capture_arguments'); }
                finally { clear(); }
            },
            toJSON() { throw new TypeError('private_preparation_capture_not_serializable'); },
            [Symbol.for('nodejs.util.inspect.custom')]() { return '<private preparation capture ' + phase + '>'; }
        };
        const result = Object.freeze(api);
        need(phase === 'CONSTRUCTING', 'preparation_capture_closed');
        phase = 'CAPTURED';
        return result;
    } catch (error) { return reject(error); }
    finally { input = bindingInput = null; }
}

module.exports = { _capturePreparation: capturePreparation, _preparationCaptureFailureCode: failureCode,
    _createPreparationCopyAttempt: createPreparationCopyAttempt,
    _takePhasedPreparationForComparison: takePhasedPreparationForComparison };

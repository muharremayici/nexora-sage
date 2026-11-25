'use strict';
// Explicit private memory model. No TypeScript, filesystem, process or image host.
const crypto = require('node:crypto');
const { performance } = require('node:perf_hooks');
const { types } = require('node:util');
class StreamStagerRefusal extends Error {
    constructor(code) { super(code); this.code = code; }
}
class CanonicalInputError extends Error {}
const fail = code => { throw new StreamStagerRefusal(code); };
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
// Ordinary facades never accept a caller-supplied context. Internal recursion
// carries this sentinel explicitly; the synthetic adapter is a separate opt-in.
const ORDINARY_CONTEXT = null;
const syntheticComparisons = new WeakMap();
const same = (a, b) => sameKernel(a, b, ORDINARY_CONTEXT);
const sameKernel = (a, b, context) => canonicalKernel(a, context) === canonicalKernel(b, context);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function scalarString(value) {
    for (let i = 0; i < value.length; i++) {
        const code = value.charCodeAt(i);
        if (code >= 0xd800 && code <= 0xdbff) {
            const next = value.charCodeAt(++i);
            if (!(next >= 0xdc00 && next <= 0xdfff)) throw new CanonicalInputError();
        } else if (code >= 0xdc00 && code <= 0xdfff) throw new CanonicalInputError();
    }
    return JSON.stringify(value);
}

function canonical(value) {
    return canonicalKernel(value, ORDINARY_CONTEXT);
}

function canonicalKernel(value, context) {
    if (value === null || typeof value === 'boolean') return JSON.stringify(value);
    if (typeof value === 'string') return scalarString(value);
    if (typeof value === 'number') {
        if (!Number.isFinite(value)) throw new CanonicalInputError();
        return JSON.stringify(value);
    }
    if (Array.isArray(value)) return '[' + value.map(item => canonicalKernel(item, context)).join(',') + ']';
    if (object(value)) return '{' + Object.keys(value)
        .sort((a, b) => compareKeys(a, b, context))
        .map(key => scalarString(key) + ':' + canonicalKernel(value[key], context)).join(',') + '}';
    throw new CanonicalInputError();
}

const hashObject = value => digest(canonical(value));
const copy = value => JSON.parse(canonical(value));
const byteSize = value => Buffer.byteLength(canonical(value), 'utf8');

// Consume only the closed schema subset used by these model definitions. Unknown
// assertions and $ref siblings fail closed, rather than being silently ignored.
function validate(value, schema, root, code) {
    return validateKernel(value, schema, root, code, ORDINARY_CONTEXT);
}

function validateKernel(value, schema, root, code, context) {
    const supported = ['type', 'required', 'properties', 'additionalProperties', 'const', 'enum',
        'items', 'minItems', 'minLength', 'minimum', 'pattern', '$ref'];
    if (!object(schema) || Object.keys(schema).some(key => !supported.includes(key))) fail('stream_stager_policy_invalid');
    if (Object.hasOwn(schema, '$ref')) {
        const name = String(schema.$ref).slice('#/$defs/'.length);
        if (Object.keys(schema).length !== 1 || !String(schema.$ref).startsWith('#/$defs/')
            || !Object.hasOwn(root.$defs, name)) fail('stream_stager_policy_invalid');
        return validateKernel(value, root.$defs[name], root, code, context);
    }
    const matches = type => type === 'null' ? value === null : type === 'integer' ? Number.isSafeInteger(value)
        : type === 'array' ? Array.isArray(value) : type === 'object' ? object(value)
        : type === 'number' ? typeof value === 'number' && Number.isFinite(value)
        : ['string', 'boolean'].includes(type) && typeof value === type;
    if (schema.type && !(Array.isArray(schema.type) ? schema.type : [schema.type]).some(matches)) fail(code);
    if (Object.hasOwn(schema, 'const') && !sameKernel(value, schema.const, context)) fail(code);
    if (schema.enum && !schema.enum.some(item => sameKernel(item, value, context))) fail(code);
    if (typeof value === 'string') {
        if (schema.minLength !== undefined && [...value].length < schema.minLength) fail(code);
        if (schema.pattern !== undefined) {
            let pattern;
            try { pattern = new RegExp(schema.pattern, 'u'); }
            catch (error) { if (error instanceof SyntaxError) fail('stream_stager_policy_invalid'); throw error; }
            if (!pattern.test(value)) fail(code);
        }
    }
    if (typeof value === 'number' && schema.minimum !== undefined && value < schema.minimum) fail(code);
    if (schema.type === 'object') {
        const props = schema.properties || {};
        if ((schema.required || []).some(key => !Object.hasOwn(value, key))) fail(code);
        for (const key of Object.keys(value)) {
            if (Object.hasOwn(props, key)) validateKernel(value[key], props[key], root, code, context);
            else if (schema.additionalProperties === false) fail(code);
            else if (object(schema.additionalProperties)) validateKernel(value[key], schema.additionalProperties, root, code, context);
        }
    }
    if (schema.type === 'array') {
        if (schema.minItems !== undefined && value.length < schema.minItems) fail(code);
        if (schema.items) for (const item of value) validateKernel(item, schema.items, root, code, context);
    }
}

const comparisonRefusals = new WeakMap();
class SyntheticComparisonRefusal extends Error {
    constructor(code) {
        super(code); this.code = code;
        // A failed registration must never publish a diagnostic classification,
        // even when a host failure happens after insertion. No source is held.
        const row = { code, ready: false };
        comparisonRefusals.set(this, row);
        row.ready = true;
    }
}
function preparationComparisonFailureCode(error) {
    const row = comparisonRefusals.get(error);
    return row && row.ready ? row.code : null;
}
const comparisonNeed = (ok, code) => { if (!ok) throw new SyntheticComparisonRefusal(code); };
const comparisonNatural = value => Number.isSafeInteger(value) && value >= 0;
const comparisonHex = value => typeof value === 'string' && value.length === 64 && /^[a-f0-9]{64}$/.test(value);
const COMPARISON_VERSION = 'v1-private-supplied-synthetic-comparison-budget';
const COMPARISON_SCOPE = 'six_supplied_synthetic_roots';
const COMPARISON_ROOTS = ['d', 't', 'i', 'a', 'i.expected_context', 'i.expected_context.entry_expected'];
const COMPARISON_CODES = ['context_policy', 'context_policy', 'context_intent', 'context_anchors', 'context_expected', 'context_expected'];

function comparisonRecord(value, keys) {
    comparisonNeed(value && typeof value === 'object' && !types.isProxy(value)
        && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null), 'comparison_metadata');
    const ds = Object.getOwnPropertyDescriptors(value), names = Reflect.ownKeys(ds);
    comparisonNeed(names.length === keys.length && names.every(k => typeof k === 'string'
        && keys.includes(k) && Object.hasOwn(ds[k], 'value')), 'comparison_metadata');
    return Object.fromEntries(keys.map(k => [k, ds[k].value]));
}
function comparisonClear(c, token) {
    c.held = null;
    syntheticComparisons.delete(token);
}
function comparisonCheckpoint(c) {
    if (c.lost) return false;
    try {
        const prefix = { scope: 'synthetic_comparison_completed_prefix',
            counters: Object.freeze({ ...c.stats }), roots: Object.freeze({ ...c.roots }) };
        if (c.phase === 'TERMINAL' || c.phase === 'COMPLETED') prefix.terminal_before_gap = c.reason;
        c.prefix = Object.freeze(prefix);
        return true;
    } catch { c.lost = true; return false; }
}
function comparisonStop(c, reason) {
    c.phase = 'TERMINAL';
    if (c.reason === null) c.reason = reason;
}
function compareKeys(a, b, context) {
    if (context === ORDINARY_CONTEXT) return Buffer.compare(Buffer.from(a, 'utf8'), Buffer.from(b, 'utf8'));
    const c = syntheticComparisons.get(context);
    comparisonNeed(c && c.phase === 'OPEN' && c.active && !c.lost, 'comparison_context_closed');
    const s = c.stats;
    s.entered++;
    const fuel = s.admitted + 1 > c.fuel, payload = s.reserved_payload + 2 * c.B > c.payload;
    if (fuel || payload) {
        s.denied++;
        c.refused = { fuel, payload };
        comparisonStop(c, 'comparison_budget');
        throw new SyntheticComparisonRefusal('comparison_budget');
    }
    // Commit the whole pair before either conversion. No failure refunds it.
    s.admitted++; s.reserved_payload += 2 * c.B;
    let stage = 'first_buffer';
    try {
        s.buffer_requests++;
        const left = Buffer.from(a, 'utf8');
        s.buffer_returns++;
        stage = 'between_buffers';
        comparisonNeed(c.phase === 'OPEN', 'comparison_reentry');
        stage = 'second_buffer'; s.buffer_requests++;
        const right = Buffer.from(b, 'utf8');
        s.buffer_returns++;
        stage = 'before_compare';
        comparisonNeed(c.phase === 'OPEN', 'comparison_reentry');
        stage = 'compare'; s.compare_requests++;
        const result = Buffer.compare(left, right);
        s.compare_returns++;
        stage = 'bookkeeping';
        comparisonNeed(c.phase === 'OPEN', 'comparison_reentry');
        comparisonNeed(comparisonCheckpoint(c), 'comparison_bookkeeping');
        return result;
    } catch (error) {
        if (stage === 'first_buffer' || stage === 'second_buffer') s.buffer_unknown++;
        if (stage === 'compare') s.compare_unknown++;
        comparisonStop(c, c.lost ? 'comparison_bookkeeping' : stage);
        throw error;
    }
}

function createSyntheticPreparationComparison(input, capDeclaration, bindingInput) {
    const bindingKeys = ['invocation_id', 'tree_sha256', 'source_sha256', 'cut'];
    const binding = comparisonRecord(bindingInput, bindingKeys);
    comparisonNeed(bindingKeys.slice(0, 3).every(k => comparisonHex(binding[k]))
        && binding.cut === COMPARISON_SCOPE, 'comparison_binding');
    const caps = comparisonRecord(capDeclaration, ['version', 'authority_kind', 'invocation_id', 'source_sha256', 'cut', 'fuel', 'payload']);
    comparisonNeed(caps.version === COMPARISON_VERSION && caps.authority_kind === 'independent_declaration'
        && ['invocation_id', 'source_sha256', 'cut'].every(k => caps[k] === binding[k])
        && comparisonNatural(caps.fuel) && comparisonNatural(caps.payload)
        && comparisonNatural(caps.fuel + 1) && comparisonNatural(2 * caps.fuel), 'comparison_caps');
    // Lazy first-party import avoids a load-time cycle: the existing copier
    // itself uses the ordinary stager facade. Never call this on the owner path.
    const capture = require('./compiler_text_window.cjs').copy;
    const charges = { nodes: 0, bytes: 0 };
    let held = capture(input, 'comparison_copy', 65536, new Set(), 0, charges);
    const B = charges.bytes;
    comparisonNeed(comparisonNatural(charges.nodes) && charges.nodes >= 1 && comparisonNatural(B)
        && charges.nodes <= 65536 && B <= 65536 && comparisonNatural(2 * B)
        && comparisonNatural(2 * B * caps.fuel), 'comparison_arithmetic');
    held = comparisonRecord(held, COMPARISON_ROOTS);
    for (const site of COMPARISON_ROOTS) held[site] = comparisonRecord(held[site], ['value', 'definition', 'schema']);
    const freeze = value => { if (value && typeof value === 'object') {
        for (const child of Object.values(value)) freeze(child); Object.freeze(value);
    } return value; };
    freeze(held);
    const result = comparisonOnOwnedRows(held, charges, caps, binding, null);
    held = null; input = capDeclaration = bindingInput = null;
    return result;
}

// Internal owned-row path. Both callers own all referenced objects; this function
// never copies or acquires a tree and never accepts an external ownership flag.
function comparisonOnOwnedRows(held, charges, caps, binding, captureReceipt) {
    let c = null, token = null, ready = false;
    try {
    const B = charges.bytes;
    const bindingKeys = ['invocation_id', 'tree_sha256', 'source_sha256', 'cut'];
    c = { held, B, fuel: caps.fuel, payload: caps.payload, phase: 'CONSTRUCTING', next: 0, active: false,
        reason: null, root_code: null, lost: false, loss_declared: false, prefix: null, refused: null,
        copy: Object.freeze({ outcome: 'returned', nodes: charges.nodes, scalar_key_charges: B, ceiling: 65536,
            starting_depth: 0, depth_ceiling: 32 }),
        stats: Object.fromEntries(['entered', 'admitted', 'denied', 'reserved_payload', 'buffer_requests', 'buffer_returns',
            'buffer_unknown', 'compare_requests', 'compare_returns', 'compare_unknown'].map(k => [k, 0])),
        roots: { entered: 0, returned: 0, threw: 0 } };
    // Transfer the sole captured tree reference into c; closures need no raw
    // caller inputs or extra local alias after this construction point.
    held = null;
    comparisonNeed(comparisonCheckpoint(c), 'comparison_bookkeeping');
    token = Object.freeze({ toJSON() { throw new TypeError('private_comparison_context_not_serializable'); },
        [Symbol.for('nodejs.util.inspect.custom')]() { return '<private supplied-synthetic comparison context>'; } });
    const requireReady = () => {
        if (!ready) {
            comparisonStop(c, 'comparison_construction_reentry'); c.held = null;
            throw new SyntheticComparisonRefusal('comparison_construction_reentry');
        }
    };
    const snapshot = () => { requireReady(); return Object.freeze({ version: COMPARISON_VERSION, scope: binding.cut,
        phase: c.phase, terminal_reason: c.reason, primary_root_code: c.root_code,
        metadata_loss: c.lost ? (c.loss_declared ? 'declared_snapshot_loss' : 'snapshot_unavailable') : null,
        ...(captureReceipt === null ? {} : { capture_binding: captureReceipt }),
        copy_charges: c.copy, counters: c.lost ? null : Object.freeze({ ...c.stats }),
        roots: c.lost ? null : Object.freeze({ ...c.roots }),
        refused_dimensions: c.refused ? Object.freeze({ ...c.refused }) : null,
        recorded_prefix: c.lost && c.prefix ? Object.freeze({ ...c.prefix,
            counters: Object.freeze({ ...c.prefix.counters }), roots: Object.freeze({ ...c.prefix.roots }) }) : null,
        final_bookkeeping_complete: c.phase === 'SEALED' && !c.lost,
        references_cleared: c.held === null, private_synthetic_guard: true,
        declared_binding: Object.freeze({ ...binding }),
        input_origin_authenticated: false, budget_authority_verified: false, source_identity_verified: false,
        current_owner_enforcement: false, compiler_loaded: false, production_enabled: false,
        semantic_lineage: false, cache_eligible: false, publication_authority: false,
        full_work_units: null, physical_fit: null, complete_cost_priced: false }); };
    const api = {
        get context() { return ready && c.phase === 'OPEN' ? token : null; },
        validate_root(handle, site, currentBinding) {
            requireReady();
            comparisonNeed(c.phase === 'OPEN', 'comparison_context_closed');
            try {
                comparisonNeed(handle === token && syntheticComparisons.get(token) === c, 'comparison_context');
                comparisonNeed(!c.active && !c.lost && typeof site === 'string'
                    && c.next < 6 && site === COMPARISON_ROOTS[c.next], 'comparison_order');
                const current = comparisonRecord(currentBinding, bindingKeys);
                comparisonNeed(bindingKeys.every(k => current[k] === binding[k]), 'comparison_binding');
                c.active = true; c.roots.entered++; c.root_code = COMPARISON_CODES[c.next];
                const row = c.held[site];
                try {
                    validateKernel(row.value, row.definition, row.schema, c.root_code, token);
                    comparisonNeed(c.phase === 'OPEN', 'comparison_reentry');
                    c.active = false; c.roots.returned++; c.next++; c.root_code = null;
                    if (c.next === 6) { c.phase = 'COMPLETED'; c.reason = 'completed'; comparisonClear(c, token); }
                    comparisonNeed(comparisonCheckpoint(c), 'comparison_bookkeeping');
                } catch (error) {
                    if (c.active) c.roots.threw++;
                    c.active = false;
                    comparisonStop(c, c.lost ? 'comparison_bookkeeping' : 'validation_threw');
                    comparisonCheckpoint(c);
                    comparisonClear(c, token);
                    throw error;
                }
            } catch (error) {
                if (c.phase === 'OPEN') {
                    comparisonStop(c, 'context_refused');
                    // A caught reentry can occur inside a still-pending native
                    // request. Let the outer operation record its settled prefix.
                    if (!c.active) comparisonCheckpoint(c);
                    comparisonClear(c, token);
                }
                throw error;
            }
        },
        snapshot,
        seal(snapshotAvailable = true) {
            requireReady();
            comparisonNeed(typeof snapshotAvailable === 'boolean', 'comparison_snapshot_metadata');
            if (c.active) {
                // An in-flight native request is not a completed prefix. Poison
                // without checkpointing; its outer call settles the accounting.
                comparisonStop(c, 'comparison_reentry'); comparisonClear(c, token);
                throw new SyntheticComparisonRefusal('comparison_reentry');
            }
            if (c.phase === 'SEALED') return snapshot();
            if (c.phase === 'OPEN' && snapshotAvailable) {
                comparisonStop(c, 'root_sequence_incomplete'); comparisonCheckpoint(c);
            }
            if (!snapshotAvailable) { c.lost = true; c.loss_declared = true; if (c.reason === null) c.reason = 'bookkeeping_interrupted'; }
            c.phase = 'SEALED'; c.active = false; comparisonClear(c, token);
            return snapshot();
        },
        toJSON() { throw new TypeError('private_comparison_adapter_not_serializable'); }
    };
    const published = Object.freeze(api);
    comparisonNeed(c.phase === 'CONSTRUCTING' && !c.lost, 'comparison_construction_reentry');
    syntheticComparisons.set(token, c);
    comparisonNeed(c.phase === 'CONSTRUCTING' && !c.lost, 'comparison_construction_reentry');
    c.phase = 'OPEN'; ready = true;
    return published;
    } catch (error) {
        if (c !== null) { c.held = null; c.phase = 'TERMINAL'; c.active = false; }
        try { if (token !== null) syntheticComparisons.delete(token); } catch { /* Preserve primary. */ }
        held = null;
        throw error;
    }
}

function preparationRows(held) {
    const own = (value, key) => {
        comparisonNeed(value !== null && typeof value === 'object' && Object.hasOwn(value, key), 'comparison_capture_shape');
        return value[key];
    };
    const i = held.intent, a = held.anchors, p = held.profiles, s = held.schemas;
    const d = own(p, 'private_compiler_entry_context_handoff'), t = own(p, 'private_compiler_entry_lifetime_trace');
    const h = own(s, 'handoff'), tr = own(s, 'trace'), en = own(s, 'entry');
    const expected = own(i, 'expected_context');
    return {
        d: { value: d, definition: own(own(h, '$defs'), 'policy'), schema: h },
        t: { value: t, definition: own(own(tr, '$defs'), 'policy'), schema: tr },
        i: { value: i, definition: own(own(h, '$defs'), 'intent'), schema: h },
        a: { value: a, definition: own(own(h, '$defs'), 'anchors'), schema: h },
        'i.expected_context': { value: expected, definition: own(own(tr, '$defs'), 'expected'), schema: tr },
        'i.expected_context.entry_expected': { value: own(expected, 'entry_expected'), definition: own(own(en, '$defs'), 'expected'), schema: en }
    };
}

const CAPTURE_SCOPE = 'six_owner_shaped_supplied_synthetic_roots';
const CAPTURE_VERSION = 'v1-private-same-capture-comparison';
const PHASED_VERSION = 'v1-private-live-phased-comparison';
const PHASED_SCOPE = 'one_live_phased_copy_six_preparation_roots';
let phasedCaptureState = 'UNINITIALIZED', phasedCaptureTake = null;

function initializePhasedPreparationComparisonCapture() {
    comparisonNeed(arguments.length === 0, 'comparison_capture_arguments');
    if (phasedCaptureState === 'READY') return;
    if (phasedCaptureState !== 'UNINITIALIZED') {
        phasedCaptureState = 'FAILED'; phasedCaptureTake = null;
        throw new SyntheticComparisonRefusal('comparison_capture_initialization');
    }
    phasedCaptureState = 'INITIALIZING';
    try {
        // Explicit setup only. Join never loads a module, even from cache.
        const owner = require('./compiler_preparation_capture.cjs');
        const take = owner._takePhasedPreparationForComparison;
        comparisonNeed(phasedCaptureState === 'INITIALIZING' && typeof take === 'function',
            'comparison_capture_initialization');
        phasedCaptureTake = take; phasedCaptureState = 'READY';
    } catch (error) {
        phasedCaptureTake = null; phasedCaptureState = 'FAILED';
        throw error;
    }
}

function joinPhasedPreparationComparison(attempt, witness, declarationInput, bindingInput) {
    comparisonNeed(phasedCaptureState === 'READY', 'comparison_capture_not_initialized');
    comparisonNeed(arguments.length === 4, 'comparison_capture_arguments');
    const bindingKeys = ['invocation_id', 'tree_sha256', 'source_sha256', 'cut'];
    const binding = comparisonRecord(bindingInput, bindingKeys);
    comparisonNeed(bindingKeys.slice(0, 3).every(k => comparisonHex(binding[k]))
        && binding.cut === PHASED_SCOPE, 'comparison_binding');
    const caps = comparisonRecord(declarationInput, ['version', 'authority_kind', 'invocation_id', 'source_sha256', 'cut', 'fuel', 'payload']);
    comparisonNeed(caps.version === PHASED_VERSION && caps.authority_kind === 'independent_declaration'
        && ['invocation_id', 'source_sha256', 'cut'].every(k => caps[k] === binding[k])
        && comparisonNatural(caps.fuel) && comparisonNatural(caps.payload)
        && comparisonNatural(caps.fuel + 1) && comparisonNatural(2 * caps.fuel), 'comparison_caps');
    let view = null, held = null, joined = null, owned = false, failed = false, primary = null;
    try {
        // Only this fixed helper authenticates the local API. Never call methods
        // on an unrecognized input, including when that helper throws.
        view = phasedCaptureTake(attempt, witness); owned = true;
        const receipt = view.copy_charges, B = receipt.scalar_key_charges;
        comparisonNeed(receipt.outcome === 'returned' && comparisonNatural(receipt.nodes) && receipt.nodes >= 1
            && receipt.nodes <= 65536 && comparisonNatural(B) && B <= 65536
            && comparisonNatural(2 * B) && comparisonNatural(2 * B * caps.fuel), 'comparison_arithmetic');
        held = view.held;
        const rows = preparationRows(held);
        const link = Object.freeze({ version: PHASED_VERSION, scope: PHASED_SCOPE,
            local_copy_witness_verified: true, same_captured_tree_references: true, copy_calls: 1,
            exact_quota_declaration: caps, quota_authority_verified: false, runtime_authority_available: false });
        joined = comparisonOnOwnedRows(rows, { nodes: receipt.nodes, bytes: B }, caps, binding, link);
    } catch (error) { failed = true; primary = error; }
    finally {
        if (owned) {
            try { attempt.release(); }
            catch (error) { if (!failed) { failed = true; primary = error; } }
        }
        if (failed && joined !== null) {
            try { joined.seal(); } catch { /* Preserve the owning primary. */ }
        }
        attempt = witness = declarationInput = bindingInput = view = held = null;
    }
    if (failed) throw primary;
    return joined;
}
// Separately disabled owner-prefix lifetime. Unlike the existing join, this
// transfers before declaration validation so the owner can inspect its same
// frozen input before admitting the six comparison roots. It is not an issuer.
function preparePhasedComparisonOwnerLease(attempt, witness) {
    comparisonNeed(phasedCaptureState === 'READY', 'comparison_capture_not_initialized');
    comparisonNeed(arguments.length === 2, 'comparison_capture_arguments');
    let view = null, held = null, receipt = null, joined = null, owned = false;
    let phase = 'CONSTRUCTING', ready = false, borrowed = false, spent = false;
    const close = () => {
        phase = 'CLOSED'; held = receipt = null;
        const context = joined;
        if (context !== null) context.seal();
        // A failed in-flight seal retains only the context cleanup obligation.
        // The owner can settle it after the pending native call unwinds.
        joined = null;
    };
    const preserve = error => { try { close(); } catch { /* Preserve primary. */ } throw error; };
    const requireReady = () => {
        if (!ready) {
            close();
            throw new SyntheticComparisonRefusal('comparison_lease_construction_reentry');
        }
    };
    try {
        // The fixed helper alone authenticates attempt/witness/receipt. An
        // unrecognized caller is never inspected or released here.
        view = phasedCaptureTake(attempt, witness); owned = true;
        held = view.held; receipt = view.copy_charges; view = null;
        // Spend the original cleanup obligation before calling it. A throwing
        // release is not retried; the captured owner itself clears in finally.
        owned = false; attempt.release();
        const api = {
            borrow() {
                try {
                    requireReady();
                    comparisonNeed(arguments.length === 0, 'comparison_lease_arguments');
                    comparisonNeed(phase === 'PREPARED', 'comparison_lease_closed');
                    borrowed = true; phase = 'BORROWED';
                    return held;
                } catch (error) { return preserve(error); }
            },
            admit(declarationInput, bindingInput) {
                try {
                    requireReady();
                    comparisonNeed(phase === 'BORROWED' && !spent, 'comparison_lease_closed');
                    // Spend before even arity/metadata inspection; no retry or
                    // refund follows a refused or interrupted admission.
                    spent = true; phase = 'ADMITTING';
                    comparisonNeed(arguments.length === 2, 'comparison_lease_arguments');
                    const keys = ['invocation_id', 'tree_sha256', 'source_sha256', 'cut'];
                    const binding = comparisonRecord(bindingInput, keys);
                    comparisonNeed(keys.slice(0, 3).every(k => comparisonHex(binding[k]))
                        && binding.cut === PHASED_SCOPE, 'comparison_binding');
                    const caps = comparisonRecord(declarationInput,
                        ['version', 'authority_kind', 'invocation_id', 'source_sha256', 'cut', 'fuel', 'payload']);
                    comparisonNeed(caps.version === PHASED_VERSION && caps.authority_kind === 'independent_declaration'
                        && ['invocation_id', 'source_sha256', 'cut'].every(k => caps[k] === binding[k])
                        && comparisonNatural(caps.fuel) && comparisonNatural(caps.payload)
                        && comparisonNatural(caps.fuel + 1) && comparisonNatural(2 * caps.fuel), 'comparison_caps');
                    const B = receipt.scalar_key_charges;
                    comparisonNeed(receipt.outcome === 'returned' && comparisonNatural(receipt.nodes) && receipt.nodes >= 1
                        && receipt.nodes <= 65536 && comparisonNatural(B) && B <= 65536
                        && comparisonNatural(2 * B) && comparisonNatural(2 * B * caps.fuel), 'comparison_arithmetic');
                    const rows = preparationRows(held);
                    const link = Object.freeze({ version: PHASED_VERSION, scope: PHASED_SCOPE,
                        local_copy_witness_verified: true, same_captured_tree_references: true, copy_calls: 1,
                        exact_quota_declaration: caps, quota_authority_verified: false, runtime_authority_available: false });
                    comparisonNeed(phase === 'ADMITTING', 'comparison_lease_closed');
                    joined = comparisonOnOwnedRows(rows, { nodes: receipt.nodes, bytes: B }, caps, binding, link);
                    // A caught reentry may have closed the lease while that
                    // constructor was publishing; settle even a returned API.
                    comparisonNeed(phase === 'ADMITTING', 'comparison_lease_closed');
                    phase = 'ADMITTED';
                    return joined;
                } catch (error) { return preserve(error); }
                finally { declarationInput = bindingInput = null; }
            },
            release() {
                requireReady();
                try { comparisonNeed(arguments.length === 0, 'comparison_lease_arguments'); }
                catch (error) { return preserve(error); }
                close();
            },
            snapshot() {
                requireReady();
                comparisonNeed(arguments.length === 0, 'comparison_lease_arguments');
                return Object.freeze({ version: PHASED_VERSION, scope: PHASED_SCOPE, phase,
                    borrowed_once: borrowed, admission_spent: spent, references_cleared: held === null && receipt === null,
                    comparison_retained: joined !== null, private_synthetic_guard: true,
                    budget_authority_verified: false, input_origin_authenticated: false, source_identity_verified: false,
                    current_owner_enforcement: false, compiler_loaded: false, production_enabled: false,
                    semantic_lineage: false, cache_eligible: false, publication_authority: false,
                    complete_cost_priced: false, full_work_units: null, physical_fit: null });
            },
            toJSON() { throw new TypeError('private_comparison_owner_lease_not_serializable'); }
        };
        const published = Object.freeze(api);
        comparisonNeed(phase === 'CONSTRUCTING', 'comparison_lease_construction_reentry');
        phase = 'PREPARED'; ready = true;
        return published;
    } catch (error) {
        if (owned) { owned = false; try { attempt.release(); } catch { /* Preserve primary. */ } }
        return preserve(error);
    } finally { attempt = witness = view = null; }
}

function comparisonOpaque(label) {
    return Object.freeze({ toJSON() { throw new TypeError('private_comparison_capability_not_serializable'); },
        [Symbol.for('nodejs.util.inspect.custom')]() { return '<private ' + label + '>'; } });
}
// No actual entry owner calls this disabled, owner-shaped supplied-data path.
function captureSyntheticPreparationComparison(input, bindingInput) {
    const bindingKeys = ['invocation_id', 'tree_sha256', 'source_sha256', 'cut'];
    const binding = comparisonRecord(bindingInput, bindingKeys);
    comparisonNeed(bindingKeys.slice(0, 3).every(k => comparisonHex(binding[k]))
        && binding.cut === CAPTURE_SCOPE, 'comparison_binding');
    const leaseOwner = require('./compiler_preparation_capture.cjs');
    let lease;
    try { lease = leaseOwner._capturePreparation(input, binding); }
    catch (error) {
        const code = leaseOwner._preparationCaptureFailureCode(error);
        if (code !== null) throw new SyntheticComparisonRefusal(code);
        throw error;
    }
    let copyReceipt;
    try { copyReceipt = lease.snapshot().copy_charges; }
    catch (error) { lease.release(); throw error; }
    const charges = { nodes: copyReceipt.nodes, bytes: copyReceipt.scalar_key_charges };
    let phase = 'CAPTURED', witness = lease.witness, quota = null, caps = null, joined = null;
    let captureReferencesCleared = false;
    input = bindingInput = null;
    const clear = () => { lease.release(); captureReferencesCleared = true; witness = null; quota = null; caps = null; };
    const current = value => {
        const row = comparisonRecord(value, bindingKeys);
        comparisonNeed(bindingKeys.every(k => row[k] === binding[k]), 'comparison_binding');
    };
    const close = () => {
        try { if (joined !== null) return joined.seal(); }
        finally { joined = null; clear(); phase = 'CLOSED'; }
    };
    const reject = error => { try { close(); } catch { /* Preserve the primary refusal. */ } throw error; };
    const owner = {
        get witness() { return phase === 'CAPTURED' || phase === 'QUOTA_DECLARED' ? witness : null; },
        issue_quota(handle, declaration, currentBinding) {
            try {
                comparisonNeed(phase === 'CAPTURED', 'comparison_capture_state');
                comparisonNeed(handle === witness, 'comparison_copy_witness');
                current(currentBinding);
                const row = comparisonRecord(declaration, ['version', 'authority_kind', 'invocation_id', 'source_sha256', 'cut', 'fuel', 'payload']);
                comparisonNeed(row.version === CAPTURE_VERSION && row.authority_kind === 'independent_declaration'
                    && ['invocation_id', 'source_sha256', 'cut'].every(k => row[k] === binding[k])
                    && comparisonNatural(row.fuel) && comparisonNatural(row.payload)
                    && comparisonNatural(row.fuel + 1) && comparisonNatural(2 * row.fuel)
                    && comparisonNatural(2 * charges.bytes * row.fuel), 'comparison_caps');
                // This token authenticates local association only, never approval.
                caps = Object.freeze(row); quota = comparisonOpaque('synthetic quota declaration'); phase = 'QUOTA_DECLARED';
                return quota;
            } catch (error) { return reject(error); }
        },
        join(handle, quotaHandle, currentBinding) {
            try {
                comparisonNeed(phase === 'QUOTA_DECLARED', 'comparison_capture_state');
                comparisonNeed(handle === witness, 'comparison_copy_witness');
                comparisonNeed(quotaHandle === quota, 'comparison_quota_witness');
                current(currentBinding);
                const view = lease.take_for_comparison(handle, currentBinding);
                comparisonNeed(view.witness === witness && view.copy_charges === copyReceipt, 'comparison_copy_witness');
                const rows = preparationRows(view.held);
                const receipt = Object.freeze({ version: CAPTURE_VERSION, scope: CAPTURE_SCOPE,
                    local_copy_witness_verified: true, same_captured_tree_references: true, copy_calls: 1,
                    exact_quota_declaration: caps, quota_authority_verified: false, runtime_authority_available: false });
                phase = 'JOINING'; // Spend before constructing a context; failure cannot reset this owner.
                joined = comparisonOnOwnedRows(rows, charges, caps, binding, receipt);
                clear(); phase = 'JOINED';
                return joined;
            } catch (error) { return reject(error); }
        },
        snapshot() { return Object.freeze({ version: CAPTURE_VERSION, scope: CAPTURE_SCOPE, phase,
            copy_charges: copyReceipt, capture_owner_references_cleared: captureReferencesCleared,
            input_origin_authenticated: false, source_identity_verified: false, budget_authority_verified: false,
            current_owner_enforcement: false, runtime_authority_available: false }); },
        close,
        toJSON() { throw new TypeError('private_comparison_capture_not_serializable'); }
    };
    return Object.freeze(owner);
}

function createCompilerStreamStager(expectedInput, profilesInput, schemaInput, options) {
    let held, descriptor, layout, image, wire, capture, bridge, expected;
    try {
        const first = profilesInput.private_compiler_stream_js_stager;
        const ceiling = profilesInput[profilesInput[first.layout_profile].image_profile].limits.metadata_bytes;
        if (!Number.isSafeInteger(ceiling) || ceiling < 1) fail('stream_stager_policy_invalid');
        const text = canonical({ expected: expectedInput, profiles: profilesInput, schema: schemaInput });
        if (Buffer.byteLength(text) > ceiling) fail('stream_stager_policy_invalid');
        held = JSON.parse(text); const { profiles, schema } = held; expected = held.expected;
        descriptor = profiles.private_compiler_stream_js_stager; layout = profiles[descriptor.layout_profile];
        image = profiles[layout.image_profile].limits; wire = profiles[layout.wire_profile];
        capture = profiles[layout.capture_profile]; bridge = profiles[wire.bridge_profile];
        for (const [value, definition] of [[descriptor, 'js_stager_policy'], [layout, 'stream_policy'],
            [profiles[descriptor.stager_profile], 'stager_policy']]) validate(value, schema.$defs[definition], schema, 'stream_stager_policy_invalid');
        validate(expected, schema.$defs.stager_expected, schema, 'stream_stager_expected_invalid');
        const limits = [image.metadata_bytes, image.capture_ms, image.compiler_files, image.compiler_bytes, image.compiler_file_bytes,
            image.image_bytes, capture.max_files, capture.max_file_bytes, capture.max_total_bytes, wire.max_request_bytes,
            bridge.max_response_bytes, bridge.max_request_id_chars];
        const schemaHash = hashObject(schema);
        if (limits.some(n => !Number.isSafeInteger(n) || n < 1) || Buffer.byteLength(text) > image.metadata_bytes
            || descriptor.schema_sha256 !== schemaHash || layout.schema_sha256 !== schemaHash
            || profiles[descriptor.stager_profile].schema_sha256 !== schemaHash) fail('stream_stager_policy_invalid');
        if (expected.session_id.length > bridge.max_request_id_chars || expected.schema_sha256 !== schemaHash
            || expected.policy_sha256 !== hashObject(layout)) fail('stream_stager_expected_invalid');
    } catch (error) {
        if (error instanceof CanonicalInputError || error instanceof TypeError) fail('stream_stager_policy_invalid');
        throw error;
    }
    const now = options?.now || (() => performance.now());
    const deadline = options?.deadline;
    const start = now();
    if (typeof deadline !== 'number' || !Number.isFinite(deadline) || typeof start !== 'number' || !Number.isFinite(start)
        || !(start < deadline && deadline <= start + image.capture_ms)) fail('stream_stager_deadline');
    const version = descriptor.version, status = descriptor.snapshot_status;
    const refs = Object.fromEntries(['context_sha256', 'inventory_sha256', 'manifest_sha256'].map(key => [key, expected[key]]));
    let state = 'NEW', code = '', incoming = Buffer.alloc(0), used = 0, manifest = null, credit = null;
    let transcript = crypto.createHash('sha256'), commitSha = null, active = null, completed = [];
    let seq = 0, fileIndex = 0, offset = 0, frameCount = 0, wireBytes = 0, rawBytes = 0, totalRaw = 0, verified = false;
    const clear = () => { held = descriptor = layout = image = wire = capture = bridge = expected = manifest = credit = transcript = active = completed = incoming = null; used = 0; };
    const reject = reason => { if (state !== 'DISPOSED') state = 'ABORTED'; code = reason; verified = false; clear(); fail(reason); };
    const check = () => {
        if (state === 'ABORTED' || state === 'DISPOSED') reject('stream_stager_order');
        const time = now();
        if (typeof time !== 'number' || !Number.isFinite(time) || time >= deadline) reject('stream_stager_deadline');
    };
    const ack = (number, frameHash, result) => ({ version: layout.protocol_version, session_id: expected.session_id,
        manifest_sha256: refs.manifest_sha256, seq: number, frame_sha256: frameHash, state: result });
    const skipEmpty = () => {
        while (fileIndex < manifest.files.length && manifest.files[fileIndex].bytes === 0) {
            completed.push(Buffer.alloc(0)); fileIndex++;
        }
    };

    function admitManifest(candidate) {
        validate(candidate, held.schema.$defs.stream_manifest, held.schema, 'stream_stager_frame_invalid');
        if (hashObject(candidate) !== refs.manifest_sha256 || candidate.version !== layout.protocol_version
            || candidate.mapping_root !== layout.mapping_root || Object.keys(expected).some(key => key !== 'manifest_sha256' && candidate[key] !== expected[key])) reject('stream_stager_frame_invalid');
        const files = candidate.files; let total = 0;
        if (files.length > Math.min(capture.max_files, image.compiler_files) || byteSize(candidate) > image.metadata_bytes) reject('stream_stager_budget');
        for (let i = 0; i < files.length; i++) {
            check(); const row = files[i]; total += row.bytes;
            if (!Number.isSafeInteger(total) || row.bytes > Math.min(capture.max_file_bytes, image.compiler_file_bytes)
                || total > Math.min(capture.max_total_bytes, image.compiler_bytes, image.image_bytes)) reject('stream_stager_budget');
            if (i && files[i - 1].name >= row.name || files.slice(0, i).some(old => old.name.toLowerCase() === row.name.toLowerCase())
                || row.bytes === 0 && row.raw_sha256 !== digest(Buffer.alloc(0))) reject('stream_stager_frame_invalid');
        }
        const offsets = files.map(() => 0); let last = -1;
        candidate.layout.forEach((frame, index) => {
            check();
            if (frame.seq !== index + 1 || frame.jsonl_bytes > wire.max_request_bytes) reject('stream_stager_frame_invalid');
            for (const row of frame.records) {
                if (row.file_index >= files.length || row.file_index < last || row.offset !== offsets[row.file_index]
                    || row.offset + row.raw_bytes > files[row.file_index].bytes) reject('stream_stager_record_invalid');
                offsets[row.file_index] += row.raw_bytes; last = row.file_index;
            }
        });
        if (offsets.some((size, i) => size !== files[i].bytes)) reject('stream_stager_record_invalid');
        return total;
    }

    function stageRecord(row, slot) {
        check(); validate(row, held.schema.$defs.stager_record, held.schema, 'stream_stager_record_invalid');
        if (!same(Object.fromEntries(Object.entries(row).filter(([key]) => !['chunk_sha256', 'utf8Base64'].includes(key))), slot)) reject('stream_stager_record_invalid');
        const raw = Buffer.from(row.utf8Base64, 'base64');
        if (raw.length !== slot.raw_bytes || raw.toString('base64') !== row.utf8Base64 || digest(raw) !== row.chunk_sha256) reject('stream_stager_raw_identity');
        if (slot.file_index !== fileIndex || slot.offset !== offset) reject('stream_stager_record_invalid');
        const file = manifest.files[fileIndex];
        if (active === null) active = Buffer.alloc(file.bytes);
        raw.copy(active, offset); offset += raw.length; rawBytes += raw.length;
        if (offset === file.bytes) {
            if (digest(active) !== file.raw_sha256) reject('stream_stager_raw_identity');
            completed.push(active); active = null; offset = 0; fileIndex++; skipEmpty();
        }
    }

    function acceptFrame(frame) {
        let value, text;
        try { text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(frame.subarray(0, -1)); }
        catch (error) { if (error instanceof TypeError) reject('stream_stager_frame_invalid'); throw error; }
        try { value = JSON.parse(text); }
        catch (error) { if (error instanceof SyntaxError) reject('stream_stager_frame_invalid'); throw error; }
        let canonicalFrame;
        try { canonicalFrame = Buffer.from(canonical(value) + '\n'); }
        catch (error) { if (error instanceof CanonicalInputError) reject('stream_stager_frame_invalid'); throw error; }
        if (!canonicalFrame.equals(frame) || !object(value) || !Number.isSafeInteger(value.seq) || value.seq !== seq) reject('stream_stager_frame_invalid');
        const kind = state === 'NEW' ? 'open' : seq <= manifest.layout.length ? 'data' : 'commit';
        if (value.kind !== kind) reject('stream_stager_order');
        validate(value, held.schema.$defs[descriptor.frame_definitions[kind]], held.schema, 'stream_stager_frame_invalid');
        if (value.version !== layout.protocol_version || value.session_id !== expected.session_id || value.manifest_sha256 !== refs.manifest_sha256) reject('stream_stager_frame_invalid');
        if (kind === 'open') {
            totalRaw = admitManifest(value.manifest); manifest = value.manifest; skipEmpty(); state = 'OPEN';
        } else if (kind === 'data') {
            const recipe = manifest.layout[seq - 1];
            if (frame.length !== recipe.jsonl_bytes || value.records.length !== recipe.records.length) reject('stream_stager_record_invalid');
            value.records.forEach((row, i) => stageRecord(row, recipe.records[i])); state = 'RECEIVING';
        } else {
            if (fileIndex !== manifest.files.length || rawBytes !== totalRaw || active !== null
                || value.files_complete !== fileIndex || value.raw_bytes_total !== totalRaw
                || value.transcript_sha256 !== transcript.copy().digest('hex')) reject('stream_stager_raw_identity');
            state = 'SEALED'; commitSha = digest(frame);
        }
        frameCount++; wireBytes += frame.length; seq++;
        if (kind === 'commit') return null;
        transcript.update(frame); credit = ack(seq - 1, digest(frame), 'STAGED'); return { ...credit };
    }

    function feed(data) {
        if (state === 'COMPLETE') reject('stream_stager_order');
        if (!Buffer.isBuffer(data)) reject('stream_stager_frame_invalid');
        if (!data.length) return null;
        if (state === 'SEALED') reject('stream_stager_trailing');
        if (credit !== null) reject('stream_stager_credit');
        const required = used + data.length;
        if (required > wire.max_request_bytes) reject('stream_stager_budget');
        const newline = data.indexOf(10);
        if (newline >= 0 && newline !== data.length - 1) reject('stream_stager_trailing');
        if (required > incoming.length) {
            const grown = Buffer.alloc(Math.min(wire.max_request_bytes, Math.max(required, incoming.length * 2)));
            incoming.copy(grown, 0, 0, used); incoming = grown;
        }
        data.copy(incoming, used); used = required;
        if (newline < 0) return null;
        const frame = incoming.subarray(0, used); const result = acceptFrame(frame);
        incoming = Buffer.alloc(0); used = 0; return result;
    }

    function operate(action, payload) {
        let settled = false, borrowing = false;
        try {
            check(); let result = null;
            if (action === 'feed') result = feed(payload);
            else if (action === 'release') {
                let size;
                try { size = byteSize(payload); }
                catch (error) { if (error instanceof CanonicalInputError) reject('stream_stager_credit'); throw error; }
                if (credit === null || size > Math.min(image.metadata_bytes, bridge.max_response_bytes)) reject('stream_stager_credit');
                validate(payload, held.schema.$defs.encoder_ack, held.schema, 'stream_stager_credit');
                if (!same(payload, credit)) reject('stream_stager_credit'); credit = null;
            } else if (action === 'eof') {
                if (state !== 'SEALED' || used || credit !== null) reject('stream_stager_incomplete');
                state = 'COMPLETE'; verified = true; result = ack(seq - 1, commitSha, 'COMPLETE');
            } else if (action === 'consume') {
                if (state !== 'COMPLETE' || typeof payload !== 'function' || types.isAsyncFunction(payload)) reject('stream_stager_order');
                state = 'BORROWING'; borrowing = true;
                result = payload(Object.freeze(completed.map((raw, i) => Object.freeze({ name: manifest.files[i].name, raw }))));
                if (result && typeof result.then === 'function') reject('stream_stager_order');
            } else reject('stream_stager_order');
            check(); if (borrowing) { state = 'DISPOSED'; clear(); }
            settled = true; return result;
        } catch (error) {
            if (!code) code = error instanceof StreamStagerRefusal && held && held.profiles[descriptor.stager_profile].refusal_codes.includes(error.code)
                ? error.code : 'stream_stager_internal';
            throw error;
        } finally {
            if (!settled) { if (state !== 'DISPOSED') state = 'ABORTED'; verified = false; clear(); }
        }
    }

    const api = {
        feed: data => operate('feed', data), release_credit: value => operate('release', value),
        end_input: () => operate('eof'), consume: consumer => operate('consume', consumer),
        dispose() { state = 'DISPOSED'; clear(); },
        snapshot() {
            return { version, status, state, code, ...refs, next_seq: seq, frames_accepted: frameCount,
                wire_bytes_accepted: wireBytes, raw_bytes_staged: rawBytes, files_completed: fileIndex, pending_bytes: used,
                credit_frame_sha256: credit?.frame_sha256 || null, raw_bytes_verified_in_model: verified,
                owned_buffer_bytes: (incoming?.length || 0) + (active?.length || 0) + (completed || []).reduce((n, raw) => n + raw.length, 0),
                whole_image_within_limit: null, peak_rss_bytes: null,
                ...Object.fromEntries(['actual_transfer', 'raw_content_verified', 'host_text_verified', 'source_freshness_verified',
                    'snapshot_bound', 'loaded_code_attested', 'native_execution_attested', 'production_enabled', 'semantic_lineage', 'cache_eligible'].map(key => [key, false])) };
        },
        toJSON() { throw new TypeError('compiler_stream_js_stager_not_serializable'); },
        [Symbol.for('nodejs.util.inspect.custom')]() { return '<private JS compiler stager ' + state + '>'; }
    };
    return Object.freeze(api);
}

module.exports = { createCompilerStreamStager, StreamStagerRefusal,
    _validateCompilerStreamValue:validate, _canonicalCompilerStreamMetadata:canonical,
    _createSyntheticPreparationComparison:createSyntheticPreparationComparison,
    _captureSyntheticPreparationComparison:captureSyntheticPreparationComparison,
    _initializePhasedPreparationComparisonCapture:initializePhasedPreparationComparisonCapture,
    _joinPhasedPreparationComparison:joinPhasedPreparationComparison,
    _preparePhasedComparisonOwnerLease:preparePhasedComparisonOwnerLease,
    _preparationComparisonFailureCode:preparationComparisonFailureCode };

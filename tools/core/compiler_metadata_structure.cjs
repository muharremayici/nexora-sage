"use strict";
// Explicit supplied-synthetic API. No graph constructor, file owner or compiler.
const crypto = require('node:crypto');
const {performance} = require('node:perf_hooks');
const {types} = require('node:util');
const {copy, CompilerTextWindowRefusal} = require('./compiler_text_window.cjs');
const {_renderOrderedAsciiContext} = require('./compiler_canonical_envelope.cjs');
const failures = new WeakMap();
const VERSION = 'v1-private-metadata-structure-checker';
const MODEL = 'metadata_structural_skeleton_u32le_v1';
const STATUS = 'STRUCTURE_LAYOUT_VERIFIED_NOT_METADATA_CONTENT_OR_OWNER';
const SCHEMA = 'config/schemas/compiler_metadata_structure.schema.json';
const LIMITS = ['metadata_bytes', 'metadata_byte_work_units', 'output_bytes', 'image_bytes', 'operation_ms'];
const SUB = ['node_count', 'property_count', 'array_edge_count', 'max_depth', 'fixed_context_bytes', 'primitive_steps'];
const SOURCES = ['implementation', 'context_binding', 'strict_copy'];
const FLAGS = ['actual_cost_attested', 'owned_phase_fit', 'private_brand_attested', 'current_permission',
    'source_freshness', 'lifetime_attested', 'physical_memory_attested', 'runtime', 'production',
    'semantic_lineage', 'cache_eligible', 'publication_authority'];
const HASH = /^[a-f0-9]{64}$/;
const NO_ROW = 0xffffffff;
const typed = Object.getPrototypeOf(Uint8Array.prototype);
const slot = name => Object.getOwnPropertyDescriptor(typed, name).get;
const byteLength = slot('byteLength'), byteOffset = slot('byteOffset'), backing = slot('buffer');
const abLength = Object.getOwnPropertyDescriptor(ArrayBuffer.prototype, 'byteLength').get;
const resizable = Object.getOwnPropertyDescriptor(ArrayBuffer.prototype, 'resizable')?.get;
const set = typed.set, alloc = Buffer.alloc, getWord = DataView.prototype.getUint32;
const bufferPrototype = Buffer.prototype;
class MetadataStructureRefusal extends Error {
    constructor(code) { super(code); this.code = code; }
}
const need = (condition, code) => { if (!condition) throw new MetadataStructureRefusal(code); };
function add(...values) {
    let result = 0;
    for (const n of values) {
        need(Number.isSafeInteger(n) && n >= 0, 'structure_arithmetic');
        result += n; need(Number.isSafeInteger(result), 'structure_arithmetic');
    }
    return result;
}
const mul = (a, b) => { add(a, b); const n = a * b; add(n); return n; };
function own(value, name) {
    need(value && typeof value === 'object' && !types.isProxy(value), 'structure_context');
    const d = Object.getOwnPropertyDescriptor(value, name);
    need(d && Object.hasOwn(d, 'value'), 'structure_context'); return d.value;
}
function exact(value, fields, code = 'structure_context') {
    need(value && typeof value === 'object' && !Array.isArray(value), code);
    const keys = Object.keys(value);
    need(keys.length === fields.length && fields.every(k => Object.hasOwn(value, k)), code);
}
function validateSyntheticMetadataStructure(input, expectedInput, profileInput, resourcesInput, schemaInput, options) {
    let context, owned, header, frames, view, result, failure, failed = false;
    let stage = 'start', work = 0, steps = 0, attemptedWork = 0, attemptedSteps = 0;
    let reserved = null, outputCeiling = null, last, started;
    const calls = {context_copy: 0, context_encode: 0, context_hash: 0, copy_attempted: 0, copy_completed: 0,
        structure_hash_attempted: 0, structure_hash_completed: 0, receipt_encode_attempted: 0,
        receipt_encode_completed: 0, receipt_hash_attempted: 0, receipt_hash_completed: 0};
    const verified = {layout_well_formed: false, nodes: 0, properties: 0, array_edges: 0, max_depth: 0};
    try {
        const now = options?.now || (() => performance.now()), deadline = options?.deadline;
        need(typeof now === 'function', 'structure_deadline');
        started = now(); last = started;
        need(Number.isFinite(started) && Number.isFinite(deadline) && started < deadline, 'structure_deadline');
        const check = () => {
            const current = now();
            need(Number.isFinite(current) && current >= last && current < deadline, 'structure_deadline'); last = current;
        };
        check(); stage = 'context';
        const ceiling = own(own(resourcesInput, 'basis'), 'metadata_bytes');
        need(Number.isSafeInteger(ceiling) && ceiling > 0, 'structure_context');
        calls.context_copy++;
        context = copy({expected: expectedInput, profile: profileInput, resources: resourcesInput, schema: schemaInput},
            'structure_context', ceiling);
        check();
        const {expected: e, profile: p, resources: r, schema: s} = context, l = p.limits, b = p.sublimits;
        exact(p, ['version', 'schema', 'schema_sha256', 'context_identity_scheme', 'default_enabled',
            'production_enabled', 'native_execution_enabled', 'semantic_lineage', 'cache_eligible', 'scope',
            'model', 'purpose', 'status', 'resource_refs', 'source_refs', 'limits', 'sublimits', 'claim_boundary']);
        exact(l, LIMITS); exact(b, SUB); exact(r, ['basis', 'planner']); exact(p.source_refs, SOURCES);
        exact(p.resource_refs, ['basis', 'planner']);
        exact(e, ['version', 'scope', 'invocation_id', 'policy_sha256', 'schema_sha256', 'source_refs',
            'resource_sha256', 'expected_structure_sha256']); exact(e.source_refs, SOURCES);
        need(p.version === VERSION && e.version === VERSION && p.model === MODEL && p.status === STATUS
            && p.schema === SCHEMA && p.scope === 'synthetic_structure_only' && e.scope === p.scope
            && p.purpose === 'validate_supplied_synthetic_structure_only'
            && p.context_identity_scheme === 'ascii_ordered_json_context_sha256_v1', 'structure_context');
        for (const k of ['default_enabled', 'production_enabled', 'native_execution_enabled', 'semantic_lineage', 'cache_eligible'])
            need(p[k] === false, 'structure_context');
        need(typeof e.invocation_id === 'string' && /^[a-zA-Z0-9_-]{1,96}$/.test(e.invocation_id), 'structure_context');
        need(typeof p.claim_boundary === 'string' && p.claim_boundary.length <= 1024, 'structure_context');
        need(p.resource_refs.basis === 'artifacts.ts_diagnostics.private_compiler_owned_view_basis.limits'
            && p.resource_refs.planner === 'artifacts.ts_diagnostics.private_compiler_owned_view_plan.limits', 'structure_context');
        for (const ref of Object.values(r)) {
            exact(ref, LIMITS);
            for (const k of LIMITS) need(Number.isSafeInteger(l[k]) && l[k] > 0 && Number.isSafeInteger(ref[k])
                && ref[k] > 0 && l[k] <= ref[k], 'structure_context');
        }
        for (const k of SUB) need(Number.isSafeInteger(b[k]) && b[k] >= (['property_count', 'array_edge_count', 'max_depth'].includes(k) ? 0 : 1), 'structure_context');
        need(b.max_depth <= 32 && b.node_count < NO_ROW && b.property_count < NO_ROW && b.array_edge_count < NO_ROW
            && b.fixed_context_bytes <= l.metadata_bytes && b.primitive_steps <= l.metadata_byte_work_units, 'structure_context');
        outputCeiling = l.output_bytes; need(deadline <= started + l.operation_ms, 'structure_deadline');
        const charge = n => {
            attemptedWork = add(work, n); need(attemptedWork <= l.metadata_byte_work_units, 'structure_work');
            work = attemptedWork; check();
        };
        const step = (n = 1) => {
            attemptedSteps = add(steps, n); need(attemptedSteps <= b.primitive_steps, 'structure_steps');
            steps = attemptedSteps; check();
        };
        const ne = Math.min(b.node_count, add(1, b.property_count, b.array_edge_count));
        const pe = Math.min(b.property_count, ne - 1), ae = ne - 1 - pe;
        const cap = add(64, mul(16, ne), mul(16, pe), mul(4, ae));
        const frameBytes = mul(12, add(b.max_depth, 1));
        reserved = add(mul(2, cap), frameBytes, 64, mul(10, b.fixed_context_bytes), mul(4, l.output_bytes));
        need(reserved <= l.metadata_bytes && reserved <= l.image_bytes, 'structure_forms');
        charge(add(mul(18, b.fixed_context_bytes), mul(4, cap), mul(8, l.output_bytes), frameBytes, 64));
        const contextNeed = (c, code) => need(c, code.replace(/^canonical_/, 'structure_'));
        const hashContext = value => {
            const wire = _renderOrderedAsciiContext(value, b.fixed_context_bytes,
                {add, mul, need: contextNeed, step, check}, calls);
            calls.context_hash++; const hash = crypto.createHash('sha256').update(wire, 'utf8').digest('hex'); check(); return hash;
        };
        for (const v of [e.policy_sha256, e.schema_sha256, e.resource_sha256, e.expected_structure_sha256,
            p.schema_sha256, ...Object.values(p.source_refs), ...Object.values(e.source_refs)])
            need(typeof v === 'string' && HASH.test(v) && v !== '0'.repeat(64), 'structure_identity');
        need(e.policy_sha256 === hashContext(p) && e.schema_sha256 === p.schema_sha256
            && e.schema_sha256 === hashContext(s) && e.resource_sha256 === hashContext(r), 'structure_identity');
        for (const k of SOURCES) need(e.source_refs[k] === p.source_refs[k], 'structure_identity');
        // Runtime owns the closed field/type checks above; supplied schema pins never grant authority.
        need(s.$id === SCHEMA && s.$defs && ['policy', 'expected', 'receipt'].every(k =>
            s.$defs[k]?.properties?.version?.const === VERSION && s.$defs[k]?.additionalProperties === false)
            && s.$defs.policy.properties.model?.const === MODEL && s.$defs.receipt.properties.status?.const === STATUS,
        'structure_schema');
        exact(s.$defs.flags?.properties, FLAGS, 'structure_schema');
        need(FLAGS.every(k => s.$defs.flags.properties[k].const === false), 'structure_schema');
        const definitions = [s];
        const keywords = ['$schema', '$id', 'title', '$defs', 'type', '$ref', 'required', 'properties',
            'additionalProperties', 'const', 'enum', 'minimum', 'maximum', 'maxLength', 'pattern'];
        while (definitions.length) {
            step(); const d = definitions.pop();
            need(d && typeof d === 'object' && !Array.isArray(d)
                && Object.keys(d).every(k => keywords.includes(k)), 'structure_schema');
            if (d.pattern) need(['^[a-f0-9]{64}$', '^[a-zA-Z0-9_-]{1,96}$'].includes(d.pattern), 'structure_schema');
            if (Object.hasOwn(d, 'const')) need(d.const === null || typeof d.const !== 'object', 'structure_schema');
            if (d.enum) need(Array.isArray(d.enum) && d.enum.every(v => v === null || typeof v !== 'object'), 'structure_schema');
            for (const group of [d.$defs, d.properties]) if (group) for (const child of Object.values(group)) definitions.push(child);
        }
        function validate(value, definition) {
            const todo = [[value, definition]];
            while (todo.length) {
                step(); const [v, d] = todo.pop();
                need(d && typeof d === 'object', 'structure_schema');
                if (d.$ref) {
                    need(Object.keys(d).length === 1 && /^#\/\$defs\/[A-Za-z_]+$/.test(d.$ref), 'structure_schema');
                    todo.push([v, s.$defs[d.$ref.slice(8)]]); continue;
                }
                if (Object.hasOwn(d, 'const')) need(v === d.const, 'structure_schema');
                if (d.enum) need(d.enum.includes(v), 'structure_schema');
                const matches = t => t === 'null' ? v === null : t === 'integer' ? Number.isSafeInteger(v)
                    : t === 'object' ? v && typeof v === 'object' && !Array.isArray(v)
                    : ['string', 'boolean'].includes(t) && typeof v === t;
                if (d.type) need((Array.isArray(d.type) ? d.type : [d.type]).some(matches), 'structure_schema');
                if (v && typeof v === 'object') {
                    need(d.type === 'object' && d.additionalProperties === false && d.properties
                        && Array.isArray(d.required), 'structure_schema');
                    const names = Object.keys(v);
                    need(names.every(k => Object.hasOwn(d.properties, k)) && d.required.every(k => Object.hasOwn(v, k)), 'structure_schema');
                    for (const k of names) todo.push([v[k], d.properties[k]]);
                } else if (typeof v === 'number') need((d.minimum === undefined || v >= d.minimum)
                    && (d.maximum === undefined || v <= d.maximum), 'structure_schema');
                else if (typeof v === 'string') need((d.maxLength === undefined || v.length <= d.maxLength)
                    && (!d.pattern || new RegExp(d.pattern).test(v)), 'structure_schema');
            }
        }
        validate(p, s.$defs.policy); validate(e, s.$defs.expected);
        stage = 'input_guard'; check();
        // Slot checks precede prototype access. No input methods/descriptors/own properties are read.
        need(input && typeof input === 'object' && !types.isProxy(input) && types.isUint8Array(input), 'structure_input');
        const size = byteLength.call(input), offset = byteOffset.call(input), buffer = backing.call(input);
        need(!types.isSharedArrayBuffer(buffer), 'structure_input');
        abLength.call(buffer); // Intrinsic brand check, without user hooks.
        need((!resizable || !resizable.call(buffer)) && Object.getPrototypeOf(input) === bufferPrototype,
            'structure_input');
        need(size >= 80 && size <= cap && offset >= 0, 'structure_length');
        stage = 'copy'; check(); calls.copy_attempted++;
        owned = alloc(size); set.call(owned, input); calls.copy_completed++; check();
        header = new Uint32Array(16); frames = new Uint32Array(3 * (b.max_depth + 1));
        view = new DataView(backing.call(owned), byteOffset.call(owned), size);
        stage = 'structure_hash'; calls.structure_hash_attempted++;
        const structureHash = crypto.createHash('sha256').update(owned).digest('hex'); calls.structure_hash_completed++; check();
        need(structureHash === e.expected_structure_sha256, 'structure_identity');
        const read = off => { step(); charge(4); return getWord.call(view, off, true); };
        const fw = (i, n) => { step(); charge(4); frames[i] = n; };
        const fr = i => { step(); charge(4); return frames[i]; };
        stage = 'header';
        for (let i = 0; i < 16; i++) { const n = read(4 * i); step(); charge(4); header[i] = n; }
        const h = i => { step(); charge(4); return header[i]; };
        const n = h(2), props = h(3), edges = h(4), declaredDepth = h(5);
        need(h(0) === 0x534d4753 && h(1) === 1 && h(6) === 0 && h(7) === 16 && h(8) === 16
            && h(9) === 4 && h(10) === 64 && h(14) === 0 && h(15) === 0, 'structure_header');
        need(n >= 1 && n <= b.node_count && props <= b.property_count && edges <= b.array_edge_count
            && n === add(1, props, edges) && declaredDepth <= b.max_depth, 'structure_counts');
        const propOffset = add(64, mul(16, n)), edgeOffset = add(propOffset, mul(16, props));
        need(h(11) === propOffset && h(12) === edgeOffset && h(13) === size
            && size === add(edgeOffset, mul(4, edges)), 'structure_length');
        const node = (id, field) => read(64 + 16 * id + 4 * field);
        stage = 'linear'; let pc = 0, ac = 0;
        for (let id = 0; id < n; id++) {
            const tag = node(id, 0), first = node(id, 1), count = node(id, 2);
            need(tag <= 5 && node(id, 3) === 0, 'structure_node');
            if (count === 0) { need(first === NO_ROW, 'structure_node'); continue; }
            need(tag === 4 || tag === 5, 'structure_node');
            const object = tag === 4, cursor = object ? pc : ac, total = object ? props : edges;
            need(first === cursor && add(first, count) <= total, 'structure_range');
            for (let j = 0; j < count; j++) {
                let child;
                if (object) {
                    const row = propOffset + 16 * (first + j);
                    need(read(row) === id && read(row + 4) === j && read(row + 12) === 0, 'structure_property');
                    child = read(row + 8);
                } else child = read(edgeOffset + 4 * (first + j));
                need(child > id && child < n, 'structure_child');
            }
            if (object) pc += count; else ac += count;
        }
        need(pc === props && ac === edges, 'structure_range');
        stage = 'preorder'; let top = 0, next = 1, depth = 0;
        fw(0, 0); fw(1, 0); fw(2, 0);
        while (top >= 0) {
            const base = top * 3, id = fr(base), ordinal = fr(base + 1), d = fr(base + 2);
            const count = node(id, 2);
            if (ordinal === count) { top--; continue; }
            const tag = node(id, 0), first = node(id, 1);
            const child = read(tag === 4 ? propOffset + 16 * (first + ordinal) + 8 : edgeOffset + 4 * (first + ordinal));
            need(child === next, 'structure_preorder'); next++;
            need(d < b.max_depth, 'structure_depth'); depth = Math.max(depth, d + 1);
            fw(base + 1, ordinal + 1); top++;
            fw(top * 3, child); fw(top * 3 + 1, 0); fw(top * 3 + 2, d + 1);
        }
        need(next === n && depth === declaredDepth, 'structure_preorder');
        Object.assign(verified, {layout_well_formed: true, nodes: n, properties: props, array_edges: edges, max_depth: depth});
        stage = 'receipt'; check(); calls.receipt_encode_attempted++;
        const body = {version: VERSION, status: STATUS, invocation_id: e.invocation_id, model: MODEL,
            policy_sha256: e.policy_sha256, schema_sha256: e.schema_sha256, source_refs: {...e.source_refs},
            resource_sha256: e.resource_sha256, structure_sha256: structureHash, structure_bytes: size,
            metadata_reserved_bytes: reserved, metadata_byte_work_units: work, primitive_steps: steps,
            structure_capacity_bytes: cap, limits: {...l}, sublimits: {...b}, verified: {...verified}, calls: {...calls},
            flags: Object.fromEntries(FLAGS.map(k => [k, false])), metadata_content_bound: false,
            full_cost_bound: null, full_capture_failure_work: null, physical_heap_rss: null};
        // Body counters are the explicit pre-settlement snapshot; terminal counters are separate.
        validate(body, s.$defs.receipt);
        const wire = JSON.stringify(body); check();
        need(Buffer.byteLength(wire, 'utf8') <= l.output_bytes, 'structure_receipt');
        const receipt = Buffer.from(wire, 'utf8'); calls.receipt_encode_completed++; check();
        calls.receipt_hash_attempted++;
        const receiptSha256 = crypto.createHash('sha256').update(receipt).digest('hex'); calls.receipt_hash_completed++; check();
        result = {receipt, receiptSha256, settlement: {calls: {...calls}, primitive_steps: steps,
            metadata_byte_work_units: work, clock_ok: true, private_refs_cleared: true}};
    } catch (error) { failure = error; failed = true; }
    finally { context = owned = header = frames = view = null; if (failed) result = null; }
    if (failed) {
        if (failure && ['object', 'function'].includes(typeof failure)) {
            const known = failure instanceof MetadataStructureRefusal
                || (failure instanceof CompilerTextWindowRefusal && failure.code === 'structure_context');
            const observation = {stage, code: known ? failure.code : 'structure_unexpected',
                metadata_byte_work_units: work, attempted_metadata_byte_work_units: attemptedWork,
                primitive_steps: steps, attempted_primitive_steps: attemptedSteps, metadata_reserved_bytes: reserved,
                calls: {...calls}, verified: {...verified}, receipt_emitted: false, structure_returned: false,
                private_refs_cleared: true, cleanup: 'references_cleared_not_physical_erasure',
                settled_within_deadline: null, full_capture_failure_work: null};
            // Observation cannot replace the original error, extend the deadline or retry serialization.
            try {
                const wire = JSON.stringify(observation);
                if (outputCeiling === null || Buffer.byteLength(wire, 'utf8') <= outputCeiling) failures.set(failure, observation);
            } catch (_) { /* Primary error identity is authoritative. */ }
        }
        throw failure;
    }
    return result;
}
function failureObservation(error) {
    const v = error && ['object', 'function'].includes(typeof error) ? failures.get(error) : null;
    return v ? {...v, calls: {...v.calls}, verified: {...v.verified}} : null;
}
module.exports = {validateSyntheticMetadataStructure, MetadataStructureRefusal, failureObservation};

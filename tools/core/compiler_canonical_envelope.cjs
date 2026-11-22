"use strict";
// Explicit synthetic-metadata API only. No default caller, filesystem or compiler.
const crypto = require('node:crypto');
const {performance} = require('node:perf_hooks');
const {types} = require('node:util');
const {copy, CompilerTextWindowRefusal} = require('./compiler_text_window.cjs');
const failures = new WeakMap();
const HASH = /^[a-f0-9]{64}$/;
const LIMITS = ['metadata_bytes', 'metadata_byte_work_units', 'output_bytes', 'image_bytes', 'operation_ms'];
const SOURCES = ['implementation', 'strict_copy', 'compatibility'];
const JOINT_SOURCES = ['implementation', 'strict_copy', 'layout_reference'];
const COMPONENTS = ['canonical_profile_sha256', 'canonical_schema_sha256', 'structure_profile_sha256', 'structure_schema_sha256'];
const SUB = ['snapshot_ceiling', 'node_count', 'property_count', 'array_edge_count', 'utf16_units',
    'payload_bytes', 'scalar_bytes', 'number_token_bytes', 'fixed_context_bytes', 'primitive_steps'];
const FLAGS = ['actual_cost_attested', 'owned_phase_fit', 'private_brand_attested', 'current_permission',
    'source_freshness', 'lifetime_attested', 'physical_memory_attested', 'runtime', 'production',
    'semantic_lineage', 'cache_eligible', 'publication_authority'];
const CONTEXT_SCHEME = 'ascii_ordered_json_context_sha256_v1';
class CanonicalEnvelopeRefusal extends Error {
    constructor(code) { super(code); this.code = code; }
}
const need = (condition, code) => { if (!condition) throw new CanonicalEnvelopeRefusal(code); };
function add(...values) {
    let result = 0;
    for (const n of values) {
        need(Number.isSafeInteger(n) && n >= 0, 'canonical_arithmetic');
        result += n;
        need(Number.isSafeInteger(result), 'canonical_arithmetic');
    }
    return result;
}
function mul(a, b) {
    add(a, b);
    const result = a * b;
    need(Number.isSafeInteger(result), 'canonical_arithmetic');
    return result;
}
function own(value, name) {
    need(value && typeof value === 'object' && !types.isProxy(value), 'canonical_context');
    const d = Object.getOwnPropertyDescriptor(value, name);
    need(d && Object.hasOwn(d, 'value'), 'canonical_context');
    return d.value;
}
function keysExactly(value, names) {
    const keys = Object.keys(value);
    need(keys.length === names.length && names.every(k => Object.hasOwn(value, k)), 'canonical_context');
}
// Private utility: captured context and trusted internal accounting controls only.
function _renderOrderedAsciiContext(value, fixedContextBytes, controls, calls) {
    const {add, mul, need, step, check} = controls;
    function walk(root, visit, keyVisit) {
        const stack = [{v: root, depth: 0, entered: false}];
        while (stack.length) {
            step();
            const f = stack[stack.length - 1];
            if (!f.entered) {
                need(f.depth <= 32, 'canonical_depth'); visit(f.v, f.depth); f.entered = true;
                if (f.v === null || typeof f.v !== 'object') { stack.pop(); continue; }
                f.array = Array.isArray(f.v); f.keys = f.array ? null : Object.keys(f.v);
                f.length = f.array ? f.v.length : f.keys.length; f.index = 0;
            }
            if (f.index === f.length) { stack.pop(); continue; }
            const index = f.index++, k = f.array ? index : f.keys[index];
            keyVisit(k, f.v, f.array);
            stack.push({v: f.v[k], depth: f.depth + 1, entered: false});
        }
    }
    let capacity = 0;
    walk(value, v => {
        capacity = add(capacity, 4);
        if (typeof v === 'string') {
            step(v.length); need(/^[\x20-\x7e]*$/.test(v), 'canonical_context');
            capacity = add(capacity, controls.tightAscii ? v.length + (v.match(/[\\"]/g) || []).length : mul(6, v.length), 2);
        } else if (typeof v === 'number') {
            need(Number.isSafeInteger(v), 'canonical_context'); capacity = add(capacity, 32);
        } else if (v && !Array.isArray(v) && typeof v === 'object') {
            const keys = Object.keys(v); let prior = null;
            for (const k of keys) {
                step(add(k.length, 1));
                need(/^[\x20-\x7e]*$/.test(k) && (controls.insertionOrder === true || prior === null || prior < k), 'canonical_context_order');
                prior = k; capacity = add(capacity, controls.tightAscii ? k.length + (k.match(/[\\"]/g) || []).length : mul(6, k.length), 4);
            }
        }
        need(capacity <= fixedContextBytes, 'canonical_context_budget');
    }, () => {});
    check(); calls.context_encode++;
    const wire = JSON.stringify(value);
    need(wire.length <= fixedContextBytes, 'canonical_context_budget'); check();
    return wire;
}
function _validateCanonicalMetadataValue(value, definition, s, controls, joint = true) {
    const {step, need} = controls;
    const todo = [[value, definition]];
    while (todo.length) {
        step(); let [v, d] = todo.pop();
        if (joint) {
            need(d && typeof d === 'object', 'canonical_schema');
            if (d.$ref) {
                need(Object.keys(d).length === 1 && /^#\/\$defs\/[A-Za-z_]+$/.test(d.$ref), 'canonical_schema');
                need(s.$defs && Object.hasOwn(s.$defs, d.$ref.slice(8)), 'canonical_schema');
                todo.push([v, s.$defs[d.$ref.slice(8)]]); continue;
            }
        }
        if (d.$ref) { need(/^#\/\$defs\/[A-Za-z_]+$/.test(d.$ref), 'canonical_schema'); d = s.$defs[d.$ref.slice(8)]; }
        need(d && typeof d === 'object', 'canonical_schema');
        if (Object.hasOwn(d, 'const')) need((d.const === null || typeof d.const !== 'object') && v === d.const, 'canonical_schema');
        if (d.enum) need(d.enum.every(x => x === null || typeof x !== 'object') && d.enum.includes(v), 'canonical_schema');
        if (d.type === 'object') {
            need(v && typeof v === 'object' && !Array.isArray(v) && d.additionalProperties === false, 'canonical_schema');
            const names = Object.keys(v); need(names.every(k => Object.hasOwn(d.properties, k))
                && d.required.every(k => Object.hasOwn(v, k)), 'canonical_schema');
            for (const k of names) todo.push([v[k], d.properties[k]]);
        } else if (d.type === 'integer') need(Number.isSafeInteger(v) && v >= d.minimum
            && (!Object.hasOwn(d, 'maximum') || v <= d.maximum), 'canonical_schema');
        else if (d.type === 'string') need(typeof v === 'string'
            && (joint ? (d.maxLength === undefined || v.length <= d.maxLength) : (!d.maxLength || v.length <= d.maxLength))
            && (!d.pattern || new RegExp(d.pattern).test(v)), 'canonical_schema');
        else if (d.type === 'boolean') need(typeof v === 'boolean', 'canonical_schema');
        else if (d.type === 'null') need(v === null, 'canonical_schema');
        else need(!d.type, 'canonical_schema');
    }
}
function encodeSyntheticCanonicalMetadata(input, expectedInput, profileInput, resourcesInput, schemaInput, options) {
    return runCanonical(input, expectedInput, profileInput, resourcesInput, schemaInput, options, false);
}
function encodeSyntheticCanonicalMetadataWithStructure(input, expectedInput, profileInput, resourcesInput, schemaInput, options) {
    return runCanonical(input, expectedInput, profileInput, resourcesInput, schemaInput, options, true);
}
function runCanonical(input, expectedInput, profileInput, resourcesInput, schemaInput, options, joint) {
    let context = null, snapshot = null, cache = null, staging = null, payload = null, receipt = null;
    let structureStaging = null, structure = null, frames = null, header = null, pairFrame = null;
    let stage = 'start', work = 0, steps = 0, attemptedWork = 0, attemptedSteps = 0;
    let metadataReserved = null, failure, failed = false;
    let outputCeiling = null, l = null, b = null, last, started;
    const calls = {context_copy: 0, context_encode: 0, context_hash: 0, capture_attempted: 0, capture_completed: 0,
        payload_encode_attempted: 0, payload_encode_completed: 0, payload_hash_attempted: 0, payload_hash_completed: 0,
        receipt_encode_attempted: 0, receipt_encode_completed: 0, receipt_hash_attempted: 0, receipt_hash_completed: 0};
    if (joint) Object.assign(calls, {structure_write_attempted: 0, structure_write_completed: 0,
        structure_hash_attempted: 0, structure_hash_completed: 0, pair_hash_attempted: 0, pair_hash_completed: 0});
    const counts = {nodes: 0, properties: 0, array_edges: 0, utf16_units: 0, max_depth: 0,
        key_encodings: 0, key_quotes: 0, comparisons: 0, comparison_byte_pairs: 0, index_moves: 0, emitted_bytes: 0};
    let result;
    try {
        let jointOptions = null;
        if (joint && options !== undefined) {
            need(options && typeof options === 'object' && !types.isProxy(options), 'canonical_context');
            need([Object.prototype, null].includes(Object.getPrototypeOf(options)), 'canonical_context');
            const ds = Object.getOwnPropertyDescriptors(options), keys = Reflect.ownKeys(ds);
            need(keys.length <= 2 && keys.every(k => ['now', 'deadline'].includes(k) && Object.hasOwn(ds[k], 'value')), 'canonical_context');
            jointOptions = ds;
        }
        const now = joint ? (jointOptions && Object.hasOwn(jointOptions, 'now') ? jointOptions.now.value : (() => performance.now()))
            : options?.now || (() => performance.now());
        const deadline = joint ? (jointOptions && Object.hasOwn(jointOptions, 'deadline') ? jointOptions.deadline.value : undefined) : options?.deadline;
        need(typeof now === 'function', 'canonical_deadline');
        started = now(); last = started;
        need(Number.isFinite(started) && Number.isFinite(deadline) && started < deadline, 'canonical_deadline');
        const check = () => {
            const current = now();
            need(Number.isFinite(current) && current >= last && current < deadline, 'canonical_deadline');
            last = current;
        };
        check(); stage = 'context';
        // Resource objects are independently supplied. No installed source is opened here.
        const ceiling = own(own(resourcesInput, 'basis'), 'metadata_bytes');
        need(Number.isSafeInteger(ceiling) && ceiling > 0, 'canonical_context');
        calls.context_copy++;
        context = copy({expected: expectedInput, profile: profileInput, resources: resourcesInput, schema: schemaInput},
            'canonical_context', ceiling);
        check();
        const {expected: e, profile: p, resources: r, schema: s} = context;
        l = p.limits; b = p.sublimits;
        keysExactly(p, ['version', 'schema', 'schema_sha256', 'context_identity_scheme', 'default_enabled',
            'production_enabled', 'native_execution_enabled', 'semantic_lineage', 'cache_eligible', 'scope',
            'model', 'layout', 'status', 'resource_refs', 'source_refs', 'limits', 'sublimits', 'claim_boundary',
            ...(joint ? ['purpose', 'component_refs', 'pair_identity_scheme'] : [])]);
        keysExactly(l, LIMITS); keysExactly(b, joint ? [...SUB, 'max_depth'] : SUB); keysExactly(r, ['basis', 'planner']);
        const sources = joint ? JOINT_SOURCES : SOURCES;
        keysExactly(p.source_refs, sources); keysExactly(p.resource_refs, ['basis', 'planner']);
        keysExactly(e, ['version', 'scope', 'invocation_id', 'policy_sha256', 'schema_sha256', 'source_refs', 'resource_sha256', ...(joint ? ['component_refs', 'intent_sha256'] : [])]);
        keysExactly(e.source_refs, sources);
        if (joint) {
            need(p.version === 'v1-private-metadata-joint-emitter' && p.scope === 'synthetic_joint_metadata_only'
                && p.model === 'same_capture_canonical_structure_u32le_v1' && p.layout === 'canonical_preorder_u32_structure_v1'
                && p.status === 'SYNTHETIC_CANONICAL_STRUCTURE_PAIR_EMITTED_NOT_OWNER_ADMISSION'
                && p.schema === 'config/schemas/compiler_metadata_joint.schema.json'
                && p.purpose === 'synthetic_joint_metadata_emission_only' && p.pair_identity_scheme === 'ascii_digest_pair_frame_v1'
                && p.context_identity_scheme === CONTEXT_SCHEME, 'canonical_context');
            keysExactly(p.component_refs, COMPONENTS); keysExactly(e.component_refs, COMPONENTS);
            need(Number.isSafeInteger(b.max_depth) && b.max_depth >= 0 && b.max_depth <= 32
                && b.node_count < 0xffffffff && b.property_count < 0xffffffff && b.array_edge_count < 0xffffffff
                && b.payload_bytes <= 0xffffffff, 'canonical_context');
        } else need(p.version === 'v2-private-canonical-envelope' && p.scope === 'synthetic_metadata_only'
            && p.model === 'bounded_key_once_canonical_metadata_number_cap_v2' && p.layout === 'named_forms_key_cache_u32_order_v1'
            && p.status === 'BOUNDED_CANONICAL_METADATA_ENCODED_NOT_OWNER_ADMISSION'
            && p.context_identity_scheme === CONTEXT_SCHEME
            && p.schema === 'config/schemas/compiler_canonical_envelope.schema.json', 'canonical_context');
        for (const name of ['default_enabled', 'production_enabled', 'native_execution_enabled', 'semantic_lineage', 'cache_eligible'])
            need(p[name] === false, 'canonical_context');
        need(e.version === p.version && e.scope === p.scope && typeof e.invocation_id === 'string'
            && /^[a-zA-Z0-9_-]{1,96}$/.test(e.invocation_id), 'canonical_context');
        need(p.resource_refs.basis === 'artifacts.ts_diagnostics.private_compiler_owned_view_basis.limits'
            && p.resource_refs.planner === 'artifacts.ts_diagnostics.private_compiler_owned_view_plan.limits', 'canonical_context');
        for (const ref of Object.values(r)) {
            keysExactly(ref, LIMITS);
            for (const k of LIMITS) need(Number.isSafeInteger(ref[k]) && ref[k] > 0
                && Number.isSafeInteger(l[k]) && l[k] > 0 && l[k] <= ref[k], 'canonical_context');
        }
        for (const [key, n] of Object.entries(b)) need(Number.isSafeInteger(n)
            && n >= (['property_count', 'array_edge_count', 'utf16_units', ...(joint ? ['max_depth'] : [])].includes(key) ? 0 : 1), 'canonical_context');
        need(b.snapshot_ceiling <= l.metadata_bytes && b.node_count <= b.snapshot_ceiling
            && b.property_count <= b.snapshot_ceiling && b.array_edge_count <= b.snapshot_ceiling
            && b.utf16_units <= l.metadata_byte_work_units && b.payload_bytes <= l.metadata_bytes
            && b.number_token_bytes <= b.scalar_bytes && b.scalar_bytes <= b.payload_bytes && b.fixed_context_bytes <= l.metadata_bytes
            && b.primitive_steps <= l.metadata_byte_work_units, 'canonical_context');
        outputCeiling = l.output_bytes;
        need(deadline <= started + l.operation_ms, 'canonical_deadline');
        const charge = n => {
            attemptedWork = add(work, n);
            need(attemptedWork <= l.metadata_byte_work_units, 'canonical_work'); work = attemptedWork; check();
        };
        const step = (n = 1) => {
            attemptedSteps = add(steps, n);
            need(attemptedSteps <= b.primitive_steps, 'canonical_steps'); steps = attemptedSteps; check();
        };
        // Fixed context has its own conservative reservation, never derived from a later receipt.
        const fixedReserve = mul(10, b.fixed_context_bytes);
        const ne = joint ? Math.min(b.node_count, add(1, b.property_count, b.array_edge_count)) : 0;
        const pe = joint ? Math.min(b.property_count, ne - 1) : 0;
        const structureCapacity = joint ? add(64, mul(16, ne), mul(16, pe), mul(4, ne - 1 - pe)) : 0;
        if (joint) need(structureCapacity <= 0xffffffff, 'canonical_forms');
        const frameBytes = joint ? mul(12, add(b.max_depth, 1)) : 0;
        const jointReserve = joint ? add(mul(2, structureCapacity), frameBytes, 64, 171) : 0;
        const baseReserve = add(mul(2, b.snapshot_ceiling), mul(8, b.property_count), mul(3, b.scalar_bytes),
            mul(2, b.payload_bytes), mul(4, l.output_bytes), fixedReserve, jointReserve);
        need(baseReserve <= l.metadata_bytes && baseReserve <= l.image_bytes, 'canonical_forms');
        metadataReserved = baseReserve;
        // Context serialization/hash, payload emission/copy/hash and receipt settlement are prepaid.
        charge(add(mul(18, b.fixed_context_bytes), mul(5, b.payload_bytes), mul(8, l.output_bytes),
            joint ? add(mul(4, structureCapacity), frameBytes, 64, 3 * 171) : 0));
        function walk(root, visit, keyVisit) {
            const stack = [{v: root, depth: 0, entered: false}];
            while (stack.length) {
                step();
                const f = stack[stack.length - 1];
                if (!f.entered) {
                    need(f.depth <= (joint ? b.max_depth : 32), 'canonical_depth'); visit(f.v, f.depth); f.entered = true;
                    if (f.v === null || typeof f.v !== 'object') { stack.pop(); continue; }
                    f.array = Array.isArray(f.v); f.keys = f.array ? null : Object.keys(f.v);
                    f.length = f.array ? f.v.length : f.keys.length; f.index = 0;
                }
                if (f.index === f.length) { stack.pop(); continue; }
                const index = f.index++, k = f.array ? index : f.keys[index];
                keyVisit(k, f.v, f.array);
                stack.push({v: f.v[k], depth: f.depth + 1, entered: false});
            }
        }
        // Context objects must already be in ASCII lexical key order. No context sorter or shared canonical call.
        function contextWire(value) {
            return _renderOrderedAsciiContext(value, b.fixed_context_bytes, {add, mul, need, step, check}, calls);
        }
        function contextHash(value) {
            const wire = contextWire(value); calls.context_hash++;
            const hash = crypto.createHash('sha256').update(wire, 'utf8').digest('hex'); check(); return hash;
        }
        for (const value of [e.policy_sha256, e.schema_sha256, e.resource_sha256, p.schema_sha256,
            ...Object.values(p.source_refs), ...Object.values(e.source_refs),
            ...(joint ? [e.intent_sha256, ...Object.values(p.component_refs), ...Object.values(e.component_refs)] : [])]) need(typeof value === 'string'
                && HASH.test(value) && value !== '0'.repeat(64), 'canonical_identity');
        need(e.policy_sha256 === contextHash(p) && e.schema_sha256 === p.schema_sha256
            && e.schema_sha256 === contextHash(s) && e.resource_sha256 === contextHash(r), 'canonical_identity');
        for (const k of sources) need(e.source_refs[k] === p.source_refs[k], 'canonical_identity');
        if (joint) for (const k of COMPONENTS) need(e.component_refs[k] === p.component_refs[k], 'canonical_identity');
        const definitions = [s];
        const schemaKeys = ['$schema', '$id', 'title', '$defs', '$ref', 'type', 'required', 'properties',
            'additionalProperties', 'const', 'enum', 'minimum', 'maximum', 'maxLength', 'pattern'];
        while (definitions.length) {
            step(); const d = definitions.pop();
            need(d && typeof d === 'object' && !Array.isArray(d)
                && Object.keys(d).every(k => schemaKeys.includes(k)), 'canonical_schema');
            if (d.pattern) need(['^[a-f0-9]{64}$', '^[a-zA-Z0-9_-]{1,96}$'].includes(d.pattern), 'canonical_schema');
            if (joint) {
                for (const k of ['minimum', 'maximum', 'maxLength']) if (Object.hasOwn(d, k))
                    need(Number.isSafeInteger(d[k]) && (k !== 'maxLength' || d[k] >= 0), 'canonical_schema');
            }
            for (const group of [d.$defs, d.properties]) if (group) for (const x of Object.values(group)) definitions.push(x);
        }
        // Deliberately small schema subset; const/enum are primitives and never re-encode input values.
        function validate(value, definition) {
            return _validateCanonicalMetadataValue(value, definition, s, {step, need}, joint);
        }
        need(s.$id === p.schema && s.$defs && s.$defs.flags, 'canonical_schema');
        need(['policy', 'expected', 'receipt'].every(k => s.$defs[k]?.properties?.version?.const === p.version)
            && s.$defs.policy?.properties?.model?.const === p.model
            && Array.isArray(s.$defs.sublimits?.required) && s.$defs.sublimits.required.includes('number_token_bytes')
            && s.$defs.sublimits?.properties?.number_token_bytes?.type === 'integer'
            && s.$defs.sublimits.properties.number_token_bytes.minimum === 1, 'canonical_schema');
        keysExactly(s.$defs.flags.properties, FLAGS);
        need(Object.values(s.$defs.flags.properties).every(v => v.const === false), 'canonical_schema');
        validate(p, s.$defs.policy); validate(e, s.$defs.expected);
        stage = 'capture'; check(); calls.capture_attempted++;
        snapshot = copy(input, 'canonical_input', b.snapshot_ceiling); calls.capture_completed++; check();
        stage = 'preflight';
        let K = 0, J = 0;
        function scanString(v) {
            let bytes = 0;
            for (let i = 0; i < v.length; i++) {
                step(); charge(2); const c = v.charCodeAt(i);
                counts.utf16_units = add(counts.utf16_units, 1);
                need(counts.utf16_units <= b.utf16_units, 'canonical_graph');
                if (c >= 0xd800 && c <= 0xdbff) {
                    need(i + 1 < v.length, 'canonical_scalar'); step(); charge(2);
                    const low = v.charCodeAt(++i);
                    counts.utf16_units = add(counts.utf16_units, 1);
                    need(low >= 0xdc00 && low <= 0xdfff, 'canonical_scalar'); bytes = add(bytes, 4);
                } else { need(c < 0xdc00 || c > 0xdfff, 'canonical_scalar'); bytes = add(bytes, c < 128 ? 1 : c < 2048 ? 2 : 3); }
                need(counts.utf16_units <= b.utf16_units, 'canonical_graph');
            }
            const capacity = add(mul(6, v.length), 2);
            need(capacity <= b.scalar_bytes, 'canonical_scalar_budget'); return {bytes, capacity};
        }
        walk(snapshot, (v, depth) => {
            counts.nodes = add(counts.nodes, 1); need(counts.nodes <= b.node_count, 'canonical_graph');
            counts.max_depth = Math.max(counts.max_depth, depth);
            if (typeof v === 'string') scanString(v);
            else if (typeof v === 'number') need(Number.isFinite(v), 'canonical_scalar');
        }, (k, parent, array) => {
            if (array) { counts.array_edges = add(counts.array_edges, 1); need(counts.array_edges <= b.array_edge_count, 'canonical_graph'); }
            else {
                counts.properties = add(counts.properties, 1); need(counts.properties <= b.property_count, 'canonical_graph');
                const x = scanString(k); K = add(K, x.bytes); J = add(J, x.capacity);
            }
        });
        metadataReserved = add(baseReserve, K, J);
        need(metadataReserved <= l.metadata_bytes && metadataReserved <= l.image_bytes, 'canonical_forms');
        stage = 'keys'; cache = new Map();
        function quote(v) {
            const cap = typeof v === 'string' ? add(mul(6, v.length), 2)
                : typeof v === 'number' ? b.number_token_bytes : v === null ? 4 : v ? 4 : 5;
            need(cap <= b.scalar_bytes, 'canonical_scalar_budget');
            charge(add(typeof v === 'string' ? mul(2, v.length) : 0, mul(5, cap))); check();
            const text = JSON.stringify(v); need(text.length <= cap, 'canonical_scalar_budget');
            const out = Buffer.from(text, 'utf8'); need(out.length <= cap, 'canonical_scalar_budget'); check(); return out;
        }
        function order(rows) {
            const n = rows.length; let from = new Uint32Array(n), to = new Uint32Array(n);
            for (let i = 0; i < n; i++) { step(); charge(4); from[i] = i; }
            let rounds = 0;
            for (let width = 1; width < n; width = mul(width, 2)) rounds++;
            const comparisons = mul(n, rounds), moves = mul(2, comparisons);
            charge(add(mul(8, comparisons), mul(8, moves))); // Upper bound for table probes/moves.
            const compare = (a, z) => {
                step(); counts.comparisons++; const x = rows[a].raw, y = rows[z].raw;
                for (let i = 0; i < Math.min(x.length, y.length); i++) {
                    step(); charge(2); counts.comparison_byte_pairs++;
                    if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1;
                }
                return x.length - y.length;
            };
            for (let width = 1; width < n; width = mul(width, 2)) {
                for (let start = 0; start < n; start += mul(2, width)) {
                    let a = start, z = Math.min(start + width, n), end = Math.min(start + 2 * width, n), i = start;
                    const middle = z;
                    while (a < middle || z < end) {
                        step(); const takeLeft = z === end || (a < middle && compare(from[a], from[z]) <= 0);
                        to[i++] = takeLeft ? from[a++] : from[z++]; counts.index_moves++;
                    }
                }
                [from, to] = [to, from];
            }
            need(counts.key_encodings <= b.property_count, 'canonical_graph'); return from;
        }
        walk(snapshot, v => {
            if (!v || typeof v !== 'object' || Array.isArray(v)) return;
            const rows = [];
            for (const key of Object.keys(v)) {
                step(); const rawCap = mul(3, key.length); charge(add(mul(2, key.length), rawCap));
                const raw = Buffer.from(key, 'utf8'); counts.key_encodings++;
                const token = quote(key); counts.key_quotes++;
                rows.push({key, raw, token});
            }
            cache.set(v, {rows, indices: order(rows)});
        }, () => {});
        stage = 'emit'; check(); calls.payload_encode_attempted++;
        staging = Buffer.alloc(b.payload_bytes); let offset = 0;
        let nodeCursor = 0, propertyCursor = 0, edgeCursor = 0, emittedDepth = 0;
        let structureBytes = 0, propertyOffset = 0, edgeOffset = 0;
        if (joint) {
            calls.structure_write_attempted++;
            propertyOffset = add(64, mul(16, counts.nodes));
            edgeOffset = add(propertyOffset, mul(16, counts.properties));
            structureBytes = add(edgeOffset, mul(4, counts.array_edges));
            need(structureBytes <= structureCapacity, 'canonical_joint_layout');
            structureStaging = Buffer.alloc(structureCapacity);
            frames = new Uint32Array(frameBytes / 4); header = new Uint32Array(16);
        }
        const wordCharge = () => { step(); charge(4); };
        const sw = (off, value) => {
            need(Number.isInteger(value) && value >= 0 && value <= 0xffffffff
                && off >= 0 && off + 4 <= structureBytes, 'canonical_joint_layout');
            wordCharge(); structureStaging.writeUInt32LE(value, off);
        };
        const fw = (i, value) => { wordCharge(); frames[i] = value; };
        const fr = i => { wordCharge(); return frames[i]; };
        function structureEnter(value, depth) {
            need(depth <= b.max_depth && nodeCursor < counts.nodes, 'canonical_joint_layout');
            const id = nodeCursor++, array = Array.isArray(value), object = value !== null && typeof value === 'object';
            const tag = value === null ? 0 : typeof value === 'boolean' ? 1 : typeof value === 'number' ? 2
                : typeof value === 'string' ? 3 : array ? 5 : 4;
            const count = object ? (array ? value.length : cache.get(value).rows.length) : 0;
            const first = count ? (array ? edgeCursor : propertyCursor) : 0xffffffff;
            if (array) edgeCursor = add(edgeCursor, count); else if (object) propertyCursor = add(propertyCursor, count);
            need(propertyCursor <= counts.properties && edgeCursor <= counts.array_edges, 'canonical_joint_layout');
            sw(64 + id * 16, tag); sw(68 + id * 16, first); sw(72 + id * 16, count); sw(76 + id * 16, 0);
            fw(3 * depth, id); fw(3 * depth + 1, first); fw(3 * depth + 2, 0);
            emittedDepth = Math.max(emittedDepth, depth);
        }
        function structureChild(depth, ordinal, array) {
            const parent = fr(3 * depth), first = fr(3 * depth + 1);
            need(fr(3 * depth + 2) === ordinal && first !== 0xffffffff && nodeCursor < counts.nodes, 'canonical_joint_layout');
            fw(3 * depth + 2, ordinal + 1);
            if (array) sw(edgeOffset + 4 * (first + ordinal), nodeCursor);
            else {
                const off = propertyOffset + 16 * (first + ordinal);
                sw(off, parent); sw(off + 4, ordinal); sw(off + 8, nodeCursor); sw(off + 12, 0);
            }
        }
        const put = token => {
            step(); need(offset + token.length <= b.payload_bytes, 'canonical_output');
            // Emission/copy/hash byte spans were prepaid; no per-depth re-encoding.
            token.copy(staging, offset); offset += token.length; counts.emitted_bytes = offset;
        };
        const punctuation = {openArray: Buffer.from('['), closeArray: Buffer.from(']'), openObject: Buffer.from('{'),
            closeObject: Buffer.from('}'), comma: Buffer.from(','), colon: Buffer.from(':')};
        const stack = [{v: snapshot, entered: false}];
        while (stack.length) {
            step(); const f = stack[stack.length - 1];
            if (!f.entered) {
                if (joint) structureEnter(f.v, stack.length - 1);
                if (f.v === null || typeof f.v !== 'object') { put(quote(f.v)); stack.pop(); continue; }
                f.array = Array.isArray(f.v); f.table = f.array ? null : cache.get(f.v);
                f.length = f.array ? f.v.length : f.table.rows.length; f.index = 0; f.entered = true;
                put(f.array ? punctuation.openArray : punctuation.openObject);
            }
            if (f.index === f.length) {
                if (joint) need(fr(3 * (stack.length - 1) + 2) === f.length, 'canonical_joint_layout');
                put(f.array ? punctuation.closeArray : punctuation.closeObject); stack.pop(); continue;
            }
            if (f.index) put(punctuation.comma);
            const i = f.index++;
            let value;
            if (f.array) value = f.v[i];
            else {
                const row = f.table.rows[f.table.indices[i]];
                put(row.token); put(punctuation.colon); value = f.v[row.key];
            }
            if (joint) structureChild(stack.length - 1, i, f.array);
            stack.push({v: value, entered: false});
        }
        calls.payload_encode_completed++; check();
        if (joint) {
            need(nodeCursor === counts.nodes && propertyCursor === counts.properties && edgeCursor === counts.array_edges
                && emittedDepth === counts.max_depth, 'canonical_joint_layout');
            const hw = (i, value) => { wordCharge(); header[i] = value; };
            hw(0, 0x534d4753); hw(1, 1); hw(2, nodeCursor); hw(3, propertyCursor); hw(4, edgeCursor);
            hw(5, emittedDepth); hw(6, 0); hw(7, 16); hw(8, 16); hw(9, 4); hw(10, 64);
            hw(11, propertyOffset); hw(12, edgeOffset); hw(13, structureBytes); hw(14, 0); hw(15, 0);
            for (let i = 0; i < 16; i++) { wordCharge(); sw(4 * i, header[i]); }
            calls.structure_write_completed++; check();
        }
        stage = 'payload_hash'; payload = Buffer.from(staging.subarray(0, offset));
        calls.payload_hash_attempted++;
        const payloadHash = crypto.createHash('sha256').update(payload).digest('hex');
        calls.payload_hash_completed++; check();
        let structureHash, pairHash;
        if (joint) {
            stage = 'structure_hash'; structure = Buffer.from(structureStaging.subarray(0, structureBytes));
            calls.structure_hash_attempted++;
            structureHash = crypto.createHash('sha256').update(structure).digest('hex'); calls.structure_hash_completed++; check();
            stage = 'pair_hash'; pairFrame = Buffer.alloc(171);
            pairFrame.write('SAGE_METADATA_CONTENT_STRUCTURE_V1\0', 0, 'ascii');
            pairFrame.writeUInt32LE(payload.length, 35); pairFrame.write(payloadHash, 39, 64, 'ascii');
            pairFrame.writeUInt32LE(structureBytes, 103); pairFrame.write(structureHash, 107, 64, 'ascii');
            check(); calls.pair_hash_attempted++;
            pairHash = crypto.createHash('sha256').update(pairFrame).digest('hex'); calls.pair_hash_completed++; check();
        }
        stage = 'receipt';
        calls.receipt_encode_attempted++;
        const body = {version: p.version, status: p.status, invocation_id: e.invocation_id,
            policy_sha256: e.policy_sha256, schema_sha256: e.schema_sha256, source_refs: {...e.source_refs},
            resource_sha256: e.resource_sha256, payload_sha256: payloadHash, payload_bytes: payload.length,
            metadata_reserved_bytes: metadataReserved, metadata_byte_work_units: work, primitive_steps: steps,
            key_cache_reserved_bytes: K, quoted_keys_reserved_bytes: J, limits: {...l}, sublimits: {...b},
            counts: {...counts}, calls: {...calls}, flags: Object.fromEntries(FLAGS.map(k => [k, false])),
            full_cost_bound: null, full_capture_failure_work: null, physical_heap_rss: null};
        if (joint) Object.assign(body, {structure_sha256: structureHash, structure_bytes: structureBytes,
            structure_capacity_bytes: structureCapacity, joint_pair_sha256: pairHash, pair_identity_scheme: p.pair_identity_scheme,
            intent_sha256: e.intent_sha256, component_refs: {...e.component_refs},
            relationship: {method: 'one_private_snapshot_one_canonical_emit_v1', content_scope: 'strict_captured_synthetic_value_only',
                object_order: 'raw_utf8_keys_v1', traversal: 'canonical_depth_first_preorder_v1',
                layout_construction_completed: true, layout_validator_completed: false, raw_json_identity_bound: false,
                external_origin_authenticated: false}});
        validate(body, s.$defs.receipt); if (!joint) body.primitive_steps = steps;
        check(); const wire = JSON.stringify(body);
        need(Buffer.byteLength(wire, 'utf8') <= l.output_bytes, 'canonical_receipt');
        const temp = Buffer.from(wire, 'utf8'); receipt = Buffer.from(temp);
        calls.receipt_encode_completed++; check(); calls.receipt_hash_attempted++;
        const receiptSha256 = crypto.createHash('sha256').update(receipt).digest('hex');
        calls.receipt_hash_completed++; check();
        result = {payload, receipt, receiptSha256, settlement: {calls: {...calls}, clock_ok: true, private_refs_cleared: true}};
        if (joint) { result.structure = structure; result.settlement.primitive_steps = steps; result.settlement.metadata_byte_work_units = work; }
    } catch (error) { failure = error; failed = true; }
    finally {
        context = snapshot = cache = staging = null;
        structureStaging = frames = header = pairFrame = null;
        if (failed) { payload = receipt = result = null; }
        if (failed) structure = null;
    }
    if (failed) {
        if (failure && ['object', 'function'].includes(typeof failure)) {
            const knownCopyRefusal = failure instanceof CompilerTextWindowRefusal
                && ['canonical_context', 'canonical_input'].includes(failure.code);
            const observation = {stage, code: failure instanceof CanonicalEnvelopeRefusal || knownCopyRefusal ? failure.code : 'canonical_unexpected',
                metadata_byte_work_units: work, attempted_metadata_byte_work_units: attemptedWork,
                primitive_steps: steps, attempted_primitive_steps: attemptedSteps, metadata_reserved_bytes: metadataReserved,
                calls: {...calls}, counts: {...counts}, receipt_emitted: false, payload_returned: false,
                private_refs_cleared: true, cleanup: 'references_cleared_not_physical_erasure',
                settled_within_deadline: null, full_capture_failure_work: null};
            // A failed/timed-out operation gets no new deadline for an observation.
            if (joint) observation.structure_returned = false;
            if (joint) {
                try {
                    const wire = JSON.stringify(observation);
                    if (outputCeiling === null || Buffer.byteLength(wire, 'utf8') <= outputCeiling) failures.set(failure, observation);
                } catch (_) { /* Preserve primary error if bounded observation settlement also fails. */ }
            } else {
                const wire = JSON.stringify(observation);
                if (outputCeiling === null || Buffer.byteLength(wire, 'utf8') <= outputCeiling) failures.set(failure, observation);
            }
        }
        throw failure;
    }
    return result;
}
function failureObservation(error) {
    const v = error && ['object', 'function'].includes(typeof error) ? failures.get(error) : null;
    return v ? {...v, counts: {...v.counts}, calls: {...v.calls}} : null;
}
module.exports = {_validateCanonicalMetadataValue, encodeSyntheticCanonicalMetadata, encodeSyntheticCanonicalMetadataWithStructure, CanonicalEnvelopeRefusal, failureObservation, _renderOrderedAsciiContext};

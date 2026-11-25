'use strict';
// Isolated ordinary-kernel compatibility adapter. Authored metadata only.
const fs = require('node:fs'), Module = require('node:module');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const api = require('../../core/compiler_stream_stager.cjs');
const canonical = api._canonicalCompilerStreamMetadata;
const validate = api._validateCompilerStreamValue;
let forbidden = 0, extraTraps = 0;
const sentinel = new RangeError('authored kernel failure');
const extra = new Proxy({}, { get() { extraTraps++; throw sentinel; } });
Module._load = () => { forbidden++; throw sentinel; };
for (const name of ['readFileSync', 'readFile', 'openSync', 'open', 'statSync', 'readdirSync',
    'writeFileSync', 'appendFileSync', 'mkdirSync']) fs[name] = () => { forbidden++; throw sentinel; };
const capture = fn => {
    try { return { ok: true, value: fn() ?? null }; }
    catch (error) { return { ok: false, code: error.code ?? null,
        constructor: error.constructor.name, original_identity: error === sentinel }; }
};
let out;
if (request.mode === 'facades') {
    out = { exports: Object.keys(api).sort(), canonical_name: canonical.name,
        canonical_arity: canonical.length, validate_name: validate.name, validate_arity: validate.length,
        direct: canonical({ z: [3, { b: 2, a: 1 }], a: 0 }, extra),
        map_results: [1, { b: 2, a: 1 }, [3]].map(canonical),
        validation_returns_undefined: validate(1, { type: 'integer' }, {}, 'caller_invalid', extra) === undefined };
} else if (request.mode === 'canonical') {
    out = capture(() => canonical(request.value, extra));
} else if (request.mode === 'special') {
    const values = { undefined: undefined, function: () => 0, bigint: 1n, symbol: Symbol(),
        nan: NaN, infinity: Infinity, negative_infinity: -Infinity,
        high_surrogate: '\ud800', low_surrogate: '\udc00' };
    out = capture(() => canonical(values[request.kind], extra));
} else if (request.mode === 'sparse') {
    const sparse = []; sparse[2] = { b: 2, a: 1 };
    out = { sparse: canonical(sparse, extra), nested: canonical([sparse, []], extra) };
} else if (request.mode === 'schema') {
    out = capture(() => validate(request.value, request.schema, request.root ?? request.schema, 'caller_invalid', extra));
} else if (request.mode === 'order') {
    const order = [], observed = (label, value) => Object.defineProperty({}, 'a', {
        enumerable: true, get() { order.push(label); return value; } });
    if (request.kind === 'const') {
        const result = capture(() => validate(observed('value', 1), { const: observed('literal', 1) }, {}, 'caller_invalid'));
        out = { result, order };
    } else if (request.kind === 'enum') {
        const later = Object.defineProperty({}, 'a', { enumerable: true, get() { throw sentinel; } });
        const result = capture(() => validate(observed('value', 1),
            { enum: [observed('wrong_literal', 0), observed('matching_literal', 1), later] }, {}, 'caller_invalid'));
        out = { result, order };
    } else {
        const result = capture(() => validate(observed('value', 1),
            { type: 'integer', const: observed('literal', 1) }, {}, 'caller_invalid'));
        out = { result, order };
    }
} else if (request.mode === 'failure') {
    let gets = 0, conversions = 0, comparisons = 0;
    const from = Buffer.from, compare = Buffer.compare;
    const value = Object.defineProperties({}, {
        z: { enumerable: true, get() { gets++; if (request.kind === 'getter') throw sentinel; return 1; } },
        a: { enumerable: true, get() { gets++; if (request.kind === 'getter') throw sentinel; return 2; } }
    });
    Buffer.from = function(...args) {
        conversions++;
        if (request.kind === 'first_buffer' && conversions === 1
            || request.kind === 'second_buffer' && conversions === 2) throw sentinel;
        return from(...args);
    };
    Buffer.compare = function(...args) {
        comparisons++;
        if (request.kind === 'compare') throw sentinel;
        return compare(...args);
    };
    try { out = { result: capture(() => canonical(value)), gets, conversions, comparisons }; }
    finally { Buffer.from = from; Buffer.compare = compare; }
} else throw new Error('unknown_authored_compatibility_case');
process.stdout.write(JSON.stringify({ result: out, extra_traps: extraTraps,
    forbidden_calls: forbidden, compiler_loaded: false }));

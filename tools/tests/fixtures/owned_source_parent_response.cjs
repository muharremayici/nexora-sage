'use strict';
// Explicit private child only. No compiler load, parser launch or global hook on import.
const crypto = require('node:crypto');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');

function installCostResponseWriter(stream, observer, loaderHook, limit) {
    if (!stream || typeof stream.write !== 'function' || !Number.isSafeInteger(limit) || limit < 1)
        throw Error('private_cost_writer_invalid');
    const original = stream.write;
    let used = false;
    function restore() {
        if (stream.write !== wrapped) return false;
        try { stream.write = original; } catch { return false; }
        return stream.write === original;
    }
    function wrapped(chunk, ...args) {
        if (used) throw Error('private_cost_response_repeated');
        used = true;
        let payload, error;
        try {
            if (typeof chunk !== 'string' || Buffer.byteLength(chunk, 'utf8') > limit || !chunk.endsWith('\n'))
                throw Error('private_cost_response_frame');
            payload = JSON.parse(chunk.slice(0, -1));
            if (Object.keys(payload).sort().join(',') !== 'batchMeta,results' || !payload.batchMeta ||
                Object.hasOwn(payload.batchMeta, 'privateSourceCost')) throw Error('private_cost_response_shape');
        } catch { error = true; }
        const loaderRestored = loaderHook.restore();
        observer.dispose();
        const counters = observer.snapshot();
        const writerRestored = restore();
        if (error || !loaderRestored || !writerRestored) throw Error('private_cost_response_unavailable');
        // The existing JSONL transport checks its normal result and text/policy hashes.
        // This additive batch metadata is a fixture observation, never hostile-code attestation.
        payload.batchMeta.privateSourceCost = {parser_payload_sha256: sha(JSON.stringify(payload.results)),
            counters, loader_restored: loaderRestored, writer_restored: writerRestored};
        const wire = JSON.stringify(payload) + '\n';
        if (Buffer.byteLength(wire, 'utf8') > limit) throw Error('private_cost_response_over_budget');
        return Reflect.apply(original, this === stream ? stream : this, [wire, ...args]);
    }
    stream.write = wrapped;
    if (stream.write !== wrapped) throw Error('private_cost_writer_not_installed');
    return Object.freeze({restore});
}

module.exports = {installCostResponseWriter};

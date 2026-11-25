'use strict';
// Private framing/decoding only; never opens a target source path.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const canonical = value => Array.isArray(value) ? '[' + value.map(canonical).join(',') + ']'
    : value && typeof value === 'object' ? '{' + Object.keys(value).sort()
        .map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}' : JSON.stringify(value);
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const exact = (obj, keys) => obj && typeof obj === 'object' && !Array.isArray(obj)
    && canonical(Object.keys(obj).sort()) === canonical(keys.slice().sort());
class OwnedTextRefusal extends Error { constructor(code) { super(code); this.code = code; } }
const refuse = code => { throw new OwnedTextRefusal(code); };

function loadPolicy(root, profile = 'private_owned_text_bridge') {
    if (!['private_owned_text_bridge','private_owned_tsx_text_bridge'].includes(profile)) refuse('owned_policy_invalid');
    const tsx = profile === 'private_owned_tsx_text_bridge';
    const policy = JSON.parse(fs.readFileSync(path.join(root, 'config/analysis_snapshot_lineage_contract.json'), 'utf8'))
        .artifacts.ts_diagnostics[profile];
    if (!exact(policy, ['version', 'schema', 'default_enabled', 'production_enabled', 'semantic_lineage',
        'max_files', 'max_source_bytes', 'max_request_bytes', 'max_response_bytes', 'max_request_id_chars', 'virtual_file', 'claim_boundary',
        ...(tsx ? ['script_kind'] : [])])
        || policy.version !== (tsx ? 'v3-owned-utf8-single-tsx' : 'v2-owned-utf8-single-ts') || policy.max_files !== 1
        || policy.schema !== (tsx ? 'config/schemas/owned_tsx_text_bridge.schema.json' : 'config/schemas/owned_text_bridge.schema.json')
        || policy.default_enabled !== false || policy.production_enabled !== false || policy.semantic_lineage !== false
        || policy.virtual_file !== (tsx ? '/__sage_owned_text__/entry.tsx' : '/__sage_owned_text__/entry.ts')
        || tsx && policy.script_kind !== 'TSX'
        || typeof policy.claim_boundary !== 'string' || !policy.claim_boundary
        || ['max_source_bytes', 'max_request_bytes', 'max_response_bytes', 'max_request_id_chars']
            .some(key => !Number.isSafeInteger(policy[key]) || policy[key] <= 0)) refuse('owned_policy_invalid');
    return Object.freeze({ ...policy });
}

function decodeRequest(raw, policy) {
    if (!Buffer.isBuffer(raw) || raw.length + 1 > policy.max_request_bytes) refuse('owned_request_over_budget');
    let text, request;
    try {
        text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw);
        request = JSON.parse(text);
    } catch { refuse('owned_request_invalid'); }
    if (!exact(request, ['protocolVersion', 'requestId', 'policySha256', 'source'])
        || request.protocolVersion !== (policy.script_kind ? 3 : 2) || typeof request.requestId !== 'string'
        || !/^[A-Za-z0-9_-]+$/.test(request.requestId) || request.requestId.length > policy.max_request_id_chars
        || request.policySha256 !== sha(canonical(policy)) || canonical(request) !== text)
        refuse('owned_request_identity');
    const source = request.source;
    if (!exact(source, ['path', 'bytes', 'sha256', 'utf8Base64', ...(policy.script_kind ? ['scriptKind'] : [])])
        || policy.script_kind && source.scriptKind !== policy.script_kind
        || source.path !== policy.virtual_file || !Number.isSafeInteger(source.bytes) || source.bytes < 0
        || source.bytes > policy.max_source_bytes || typeof source.sha256 !== 'string'
        || !/^[a-f0-9]{64}$/.test(source.sha256) || typeof source.utf8Base64 !== 'string'
        || source.utf8Base64.length > Math.ceil(policy.max_source_bytes / 3) * 4
        || source.utf8Base64.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(source.utf8Base64))
        refuse('owned_source_invalid');
    const bytes = Buffer.from(source.utf8Base64, 'base64');
    if (bytes.length !== source.bytes || bytes.toString('base64') !== source.utf8Base64
        || sha(bytes) !== source.sha256) refuse('owned_source_identity');
    let sourceText;
    try { sourceText = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes); }
    catch { refuse('owned_encoding_refused'); }
    return Object.freeze({ requestId: request.requestId, path: source.path, text: sourceText,
        ...(policy.script_kind ? {scriptKind:policy.script_kind} : {}),
        sourceSha256: source.sha256, policySha256: request.policySha256 });
}

function readSingleRequest(stream, policy, accept, reject) {
    let parts = [], size = 0, finished = false;
    const stop = () => { finished = true; parts = []; stream.destroy(); };
    const fail = code => { if (!finished) { stop(); reject(code); } };
    stream.on('data', chunk => {
        if (finished) return;
        const newline = chunk.indexOf(10);
        const piece = newline < 0 ? chunk : chunk.subarray(0, newline);
        size += piece.length;
        if (size + 1 > policy.max_request_bytes) { fail('owned_request_over_budget'); return; }
        parts.push(piece);
        if (newline >= 0) {
            if (newline !== chunk.length - 1) { fail('owned_multiple_requests_refused'); return; }
            try {
                const owned = decodeRequest(Buffer.concat(parts, size), policy);
                stop(); accept(owned);
            } catch (error) { fail(error instanceof OwnedTextRefusal ? error.code : 'owned_request_invalid'); }
        }
    });
    stream.on('end', () => { if (!finished) fail('owned_request_incomplete'); });
    stream.on('error', () => fail('owned_input_stream_error'));
}

module.exports = { loadPolicy, readSingleRequest, OwnedTextRefusal };

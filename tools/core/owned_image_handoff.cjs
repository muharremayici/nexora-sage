'use strict';
// Pure private composition. The parent owns actual parser/Atlas validation;
// installation inputs come from the controlled fixture, never request paths.
const crypto = require('node:crypto');
const { createExperiment, hashObject } = require('./ts_input_image_experiment.cjs');
const { prepareCompilerInputObservation } = require('./compiler_input_observation.cjs');
const digest = value => crypto.createHash('sha256').update(value).digest('hex');
const canonical = value => Array.isArray(value) ? '[' + value.map(canonical).join(',') + ']'
    : value && typeof value === 'object' ? '{' + Object.keys(value).sort()
        .map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}' : JSON.stringify(value);
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
    && canonical(Object.keys(value).sort()) === canonical(keys.slice().sort());
const isSha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
class JoinedRefusal extends Error { constructor(code) { super(code); this.code = code; } }
const refuse = code => { throw new JoinedRefusal(code); };

function admitHandoff(ts, request, expected, policies, compilerReference) {
    const { joined, bridge, image } = policies;
    if (!exact(joined, ['version', 'schema', 'default_enabled', 'production_enabled', 'semantic_lineage',
        'max_source_files', 'bridge_profile', 'image_profile', 'parser_virtual_path', 'compiler_virtual_path',
        'atlas_relative_path', 'input_observation_version', 'input_observation_schema',
        'input_observation_authority', 'claim_boundary'])
        || joined.version !== 'v1-private-joined-same-text' || joined.max_source_files !== 1
        || joined.default_enabled !== false || joined.production_enabled !== false || joined.semantic_lineage !== false
        || joined.parser_virtual_path !== bridge.virtual_file
        || joined.compiler_virtual_path !== '/__sage_ts_image__/project/entry.ts'
        || joined.atlas_relative_path !== 'entry.ts'
        || bridge.default_enabled !== false || bridge.production_enabled !== false || bridge.semantic_lineage !== false
        || image.default_enabled !== false || image.production_enabled !== false || image.lineage_enabled !== false)
        refuse('joined_policy_invalid');
    if (!exact(request, ['version', 'binding', 'source'])
        || request.version !== joined.version || !exact(request.source, ['bytes', 'sha256', 'utf8Base64']))
        refuse('joined_request_invalid');
    const binding = request.binding;
    checkSourceBinding(ts, binding, expected, policies, compilerReference);
    const { raw, text } = decodeOwnedSource(ts, request.source, binding, policies);
    const frozen = JSON.parse(canonical(binding));
    return Object.freeze({ raw, text, binding: Object.freeze(frozen), bindingSha256: hashObject(frozen) });
}

function checkSourceBinding(ts, binding, expected, policies, compilerReference, extraKeys = []) {
    const { joined, bridge, image } = policies;
    const keys = ['invocation_id', 'atlas_snapshot_id', 'atlas_sha256', 'parser_response_sha256',
        'bridge_policy_sha256', 'image_policy_sha256', 'handoff_policy_sha256', 'source_sha256', 'source_bytes',
        'parser_virtual_path', 'compiler_virtual_path', 'atlas_relative_path', 'compiler_version', 'compiler_source_sha256',
        ...extraKeys];
    if (!exact(binding, keys) || !exact(expected, keys) || hashObject(binding) !== hashObject(expected))
        refuse('joined_binding_mismatch');
    if (keys.filter(key => key.endsWith('_sha256') || key === 'atlas_snapshot_id').some(key => !isSha(binding[key]))
        || typeof binding.invocation_id !== 'string' || !/^[A-Za-z0-9_-]+$/.test(binding.invocation_id)
        || binding.invocation_id.length > bridge.max_request_id_chars
        || binding.bridge_policy_sha256 !== hashObject(bridge) || binding.image_policy_sha256 !== hashObject(image)
        || binding.handoff_policy_sha256 !== hashObject(joined)
        || binding.parser_virtual_path !== joined.parser_virtual_path
        || binding.compiler_virtual_path !== joined.compiler_virtual_path
        || binding.atlas_relative_path !== joined.atlas_relative_path
        || binding.compiler_version !== ts.version || binding.compiler_source_sha256 !== compilerReference)
        refuse('joined_context_refused');
}

function decodeOwnedSource(ts, source, binding, policies) {
    const { raw, text } = decodeOwnedSourceBytes(source, binding, policies);
    const { joined } = policies;
    const parsed = ts.createSourceFile(joined.compiler_virtual_path, text, ts.ScriptTarget.Latest, true);
    const pre = ts.preProcessFile(text, true, true);
    if (parsed.parseDiagnostics.length || pre.importedFiles.length || pre.referencedFiles.length
        || pre.typeReferenceDirectives.length || parsed.isDeclarationFile
        || /(^|[\r\n])\s*\/\/\/\s*<reference\b/.test(text))
        refuse('joined_source_scope_refused');
    return { raw, text };
}

function decodeOwnedSourceBytes(source, binding, policies) {
    const { joined, bridge, image } = policies;
    if (!exact(source, ['bytes', 'sha256', 'utf8Base64'])) refuse('joined_source_invalid');
    if (!Number.isSafeInteger(source.bytes) || source.bytes < 0
        || source.bytes > Math.min(bridge.max_source_bytes, image.limits.source_file_bytes)
        || binding.source_bytes !== source.bytes || binding.source_sha256 !== source.sha256
        || typeof source.utf8Base64 !== 'string'
        || source.utf8Base64.length > Math.ceil(bridge.max_source_bytes / 3) * 4
        || source.utf8Base64.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(source.utf8Base64))
        refuse('joined_source_invalid');
    const raw = Buffer.from(source.utf8Base64, 'base64');
    if (raw.length !== source.bytes || digest(raw) !== source.sha256
        || raw.toString('base64') !== source.utf8Base64) refuse('joined_source_identity');
    let text;
    try { text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw); }
    catch { refuse('joined_encoding_refused'); }
    return { raw, text };
}

function createJoinedExperiment(ts, admitted, policies, compilerEntries) {
    const { joined, image } = policies;
    const outer = admitted.binding, outerIdentity = admitted.bindingSha256;
    if (hashObject(outer) !== outerIdentity || digest(admitted.raw) !== outer.source_sha256
        || admitted.raw.toString('utf8') !== admitted.text
        || hashObject(joined) !== outer.handoff_policy_sha256
        || hashObject(image) !== outer.image_policy_sha256
        || hashObject(policies.bridge) !== outer.bridge_policy_sha256)
        refuse('joined_admitted_input_changed');
    const parserTextIdentity = digest(admitted.text);
    const hostTextIdentity = digest(admitted.text.replace(/^\ufeff/, ''));
    const inner = { atlas_snapshot_id: outer.atlas_snapshot_id, atlas_sha256: outer.atlas_sha256,
        producer_run_id: outer.invocation_id, policy_sha256: outer.image_policy_sha256,
        compiler_version: outer.compiler_version, compiler_source_sha256: outer.compiler_source_sha256,
        source_sha256: { [joined.compiler_virtual_path]: outer.source_sha256 } };
    const config = { files: [joined.atlas_relative_path], compilerOptions: image.compiler_options };
    const configRaw = Buffer.from(JSON.stringify(config));
    const inputs = prepareCompilerInputObservation(compilerEntries, configRaw, {
        source_path: joined.compiler_virtual_path, source_host_sha256: hostTextIdentity,
        source_bytes: outer.source_bytes, handoff_sha256: outerIdentity,
    }, policies);
    const entries = [
        { path: joined.compiler_virtual_path, role: 'source', raw: admitted.raw },
        { path: '/__sage_ts_image__/project/tsconfig.json', role: 'auxiliary', raw: configRaw },
        ...compilerEntries,
    ];
    const instance = createExperiment(ts, { entries, namespace_complete: true }, inner, image);
    let disposed = false;
    return Object.freeze({
        replay(expected) {
            if (disposed) refuse('joined_disposed');
            if (hashObject(expected) !== outerIdentity) refuse('joined_expected_binding_mismatch');
            const result = instance.replay(inner);
            const inputObservation = inputs.project(result, instance.summary);
            return { ...result, handoff_sha256: outerIdentity,
                compiler_input_observation: inputObservation,
                atlas_snapshot_id: outer.atlas_snapshot_id, atlas_sha256: outer.atlas_sha256,
                source_sha256: outer.source_sha256,
                parser_response_sha256: outer.parser_response_sha256,
                bridge_policy_sha256: outer.bridge_policy_sha256, image_policy_sha256: outer.image_policy_sha256,
                mapping_sha256: hashObject({ parser: outer.parser_virtual_path, compiler: outer.compiler_virtual_path,
                    atlas: outer.atlas_relative_path }),
                parser_text_sha256: parserTextIdentity,
                expected_host_text_sha256: hostTextIdentity,
                scope: 'synthetic_same_text_only', native_project_equivalent: false };
        },
        dispose() { disposed = true; instance.dispose(); },
    });
}

module.exports = { admitHandoff, createJoinedExperiment, JoinedRefusal, canonical,
    checkSourceBinding, decodeOwnedSource, decodeOwnedSourceBytes };

'use strict';
const crypto = require('node:crypto');
const { createExperiment, hashObject, admitCompilerPackageMetadata } = require('./ts_input_image_experiment.cjs');
const { prepareCompilerInputObservation } = require('./compiler_input_observation.cjs');
const { checkSourceBinding, decodeOwnedSource, canonical, JoinedRefusal } = require('./owned_image_handoff.cjs');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const fail = code => { throw new JoinedRefusal(code); };
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const AUX_KEYS = ['auxiliary_atlas_path', 'auxiliary_virtual_path', 'auxiliary_raw_sha256',
    'auxiliary_host_sha256', 'auxiliary_context_sha256', 'auxiliary_bytes'];

function admitDeclarationHandoff(ts, request, expected, policies, compilerReference) {
    const { joined, bridge } = policies;
    if (joined.version !== 'v2-private-single-declaration' || joined.max_source_files !== 1
        || joined.max_auxiliary_files !== 1 || joined.default_enabled !== false
        || joined.production_enabled !== false || joined.semantic_lineage !== false
        || bridge.default_enabled !== false || bridge.production_enabled !== false || bridge.semantic_lineage !== false
        || policies.image.default_enabled !== false || policies.image.production_enabled !== false
        || policies.image.lineage_enabled !== false
        || joined.auxiliary_atlas_path !== 'types.d.ts'
        || joined.auxiliary_virtual_path !== '/__sage_ts_image__/project/types.d.ts'
        || joined.parser_virtual_path !== bridge.virtual_file
        || !Number.isSafeInteger(joined.max_auxiliary_bytes) || joined.max_auxiliary_bytes <= 0)
        fail('declaration_policy_refused');
    if (!exact(request, ['version', 'binding', 'source', 'auxiliary']) || request.version !== joined.version)
        fail('declaration_request_refused');
    const binding = request.binding;
    checkSourceBinding(ts, binding, expected, policies, compilerReference, AUX_KEYS);
    const source = decodeOwnedSource(ts, request.source, binding, policies);
    const raw = decodeOwnedDeclaration(ts, request.auxiliary, binding, policies);
    const frozen = Object.freeze(JSON.parse(canonical(binding)));
    return Object.freeze({ ...source, auxiliaryRaw: raw, binding: frozen, bindingSha256: hashObject(frozen) });
}

function decodeOwnedDeclaration(ts, auxiliary, binding, policies) {
    const raw = decodeOwnedDeclarationBytes(auxiliary, binding, policies);
    const { joined } = policies;
    const text = raw.toString('utf8').replace(/^\ufeff/, '');
    const parsed = ts.createSourceFile(joined.auxiliary_virtual_path, text, ts.ScriptTarget.Latest, true);
    const pre = ts.preProcessFile(text, true, true);
    if (!parsed.isDeclarationFile || parsed.parseDiagnostics.length || ts.isExternalModule(parsed)
        || pre.importedFiles.length || pre.referencedFiles.length || pre.typeReferenceDirectives.length
        || /(^|[\r\n])\s*\/\/\/\s*<reference\b/.test(text)) fail('declaration_scope_refused');
    return raw;
}

function decodeOwnedDeclarationBytes(auxiliary, binding, policies) {
    const { joined } = policies;
    if (!exact(auxiliary, ['bytes', 'raw_sha256', 'host_text_sha256', 'utf8Base64'])
        || binding.auxiliary_atlas_path !== joined.auxiliary_atlas_path
        || binding.auxiliary_virtual_path !== joined.auxiliary_virtual_path
        || !Number.isSafeInteger(auxiliary.bytes) || auxiliary.bytes < 0
        || auxiliary.bytes > Math.min(joined.max_auxiliary_bytes, policies.image.limits.auxiliary_file_bytes)
        || binding.auxiliary_bytes !== auxiliary.bytes
        || binding.auxiliary_raw_sha256 !== auxiliary.raw_sha256
        || binding.auxiliary_host_sha256 !== auxiliary.host_text_sha256
        || typeof auxiliary.utf8Base64 !== 'string'
        || auxiliary.utf8Base64.length > Math.ceil(joined.max_auxiliary_bytes / 3) * 4
        || auxiliary.utf8Base64.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(auxiliary.utf8Base64))
        fail('declaration_input_refused');
    const raw = Buffer.from(auxiliary.utf8Base64, 'base64');
    if (raw.length !== auxiliary.bytes || sha(raw) !== auxiliary.raw_sha256
        || raw.toString('base64') !== auxiliary.utf8Base64) fail('declaration_identity_refused');
    let text;
    try { text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw).replace(/^\ufeff/, ''); }
    catch { fail('declaration_encoding_refused'); }
    if (sha(text) !== auxiliary.host_text_sha256) fail('declaration_host_text_refused');
    return raw;
}

function createDeclarationExperiment(ts, admitted, policies, compilerEntries, metadataInput = null) {
    const { joined, image, bridge } = policies, outer = admitted.binding;
    if (hashObject(outer) !== admitted.bindingSha256 || sha(admitted.raw) !== outer.source_sha256
        || admitted.raw.toString('utf8') !== admitted.text || sha(admitted.auxiliaryRaw) !== outer.auxiliary_raw_sha256
        || hashObject(joined) !== outer.handoff_policy_sha256 || hashObject(image) !== outer.image_policy_sha256
        || hashObject(bridge) !== outer.bridge_policy_sha256) fail('declaration_admitted_input_changed');
    const parserHash = sha(admitted.text), sourceHostHash = sha(admitted.text.replace(/^\ufeff/, ''));
    const projectSources=sourceRecords(admitted,joined);
    if(joined.version==='v11-private-react-barrel'&&Object.values(outer.sources).some(row=>
        !policies.bridges?.[row.bridge_profile]||hashObject(policies.bridges[row.bridge_profile])!==row.bridge_policy_sha256))
        fail('barrel_bridge_changed');
    const inner = { atlas_snapshot_id: outer.atlas_snapshot_id, atlas_sha256: outer.atlas_sha256,
        producer_run_id: outer.invocation_id, policy_sha256: outer.image_policy_sha256,
        compiler_version: outer.compiler_version, compiler_source_sha256: outer.compiler_source_sha256,
        source_sha256: projectSources?Object.fromEntries(Object.values(projectSources).map(row=>[row.path,row.raw_sha256])):{ [joined.compiler_virtual_path]: outer.source_sha256 } };
    const metadata = metadataInput === null ? null : admitCompilerPackageMetadata(ts, metadataInput, {
        source_handoff_sha256:admitted.bindingSha256,compiler_version:outer.compiler_version,
        compiler_source_sha256:outer.compiler_source_sha256});
    if (metadata) {
        Object.assign(inner,{compiler_metadata_sha256:metadata.expected.raw_sha256,
            compiler_metadata_policy_sha256:metadata.expected.policy_sha256,source_handoff_sha256:admitted.bindingSha256});
        compilerEntries = compilerEntries.map(entry => {
            if (typeof entry.path !== 'string' || !/^\/__sage_ts_image__\/compiler\/lib[a-zA-Z0-9_.-]*\.d\.ts$/.test(entry.path))
                fail('compiler_metadata_library_layout_refused');
            return {...entry,path:metadata.policy.library_virtual_root+'/'+entry.path.split('/').pop()};
        });
    }
    const hasPackages=['v5-private-react-input-summary','v6-private-react-two-source-input','v7-private-react-barrel-input'].includes(joined.input_observation_version);
    const hasJsx = joined.input_observation_version === 'v4-private-tsx-input-summary';
    const capturedConfig = hasPackages || hasJsx || joined.input_observation_version === 'v3-private-captured-config-summary';
    if(hasJsx&&(!Buffer.isBuffer(admitted.jsxRaw)||sha(admitted.jsxRaw)!==outer.jsx_raw_sha256
        ||admitted.jsxRaw.length!==outer.jsx_bytes||outer.jsx_context_sha256!==outer.config_context_sha256))
        fail('jsx_admitted_input_changed');
    if (capturedConfig && (!Buffer.isBuffer(admitted.configRaw) || sha(admitted.configRaw) !== outer.config_raw_sha256
        || admitted.configRaw.length !== outer.config_bytes || outer.config_context_sha256 !== outer.auxiliary_context_sha256))
        fail('config_admitted_input_changed');
    const configRaw = capturedConfig ? admitted.configRaw : Buffer.from(JSON.stringify({
        files: [joined.atlas_relative_path, joined.auxiliary_atlas_path], compilerOptions: image.compiler_options }));
    const declaration = { path: joined.auxiliary_virtual_path, raw: admitted.auxiliaryRaw,
        atlas_context_sha256: outer.auxiliary_context_sha256 };
    const inputs = prepareCompilerInputObservation(compilerEntries, configRaw, {
        source_path: joined.compiler_virtual_path, source_host_sha256: sourceHostHash,
        source_bytes: outer.source_bytes, handoff_sha256: admitted.bindingSha256, declaration,
        ...(projectSources?{sources:projectSources}:{}),
        ...(capturedConfig ? { config: { path: joined.config_virtual_path, atlas_context_sha256: outer.config_context_sha256 } } : {}),
        ...(hasJsx ? {jsx_declaration:{path:joined.jsx_virtual_path,raw:admitted.jsxRaw,atlas_context_sha256:outer.jsx_context_sha256}} : {}),
        ...(hasPackages?{typing_packages:admitted.typingPackages}:{}),
    }, policies, metadata);
    const instance = createExperiment(ts, { namespace_complete: true, entries: [
        ...(projectSources?Object.entries(projectSources).map(([key,row])=>({path:row.path,role:'source',raw:admitted.sourceInputs[key].raw}))
            :[{ path: joined.compiler_virtual_path, role: 'source', raw: admitted.raw }]),
        { path: '/__sage_ts_image__/project/tsconfig.json', role: 'auxiliary', raw: configRaw },
        { path: joined.auxiliary_virtual_path, role: 'auxiliary', raw: admitted.auxiliaryRaw },
        ...(hasJsx ? [{path:joined.jsx_virtual_path,role:'auxiliary',raw:admitted.jsxRaw}] : []),
        ...(hasPackages?admitted.typingPackages.entries.map(({path,raw})=>({path,raw,role:'auxiliary'})):[]), ...compilerEntries,
    ] }, inner, image, metadata,hasPackages?admitted.typingPackages:null);
    const identity = admitted.bindingSha256;
    let disposed = false;
    return Object.freeze({
        inputIdentity(expected) {
            if (disposed) fail('declaration_disposed');
            if (hashObject(expected) !== identity) fail('declaration_expected_binding_mismatch');
            return instance.summary;
        },
        replay(expected) {
            if (disposed) fail('declaration_disposed');
            if (hashObject(expected) !== identity) fail('declaration_expected_binding_mismatch');
            const result = instance.replay(inner);
            return { ...result, handoff_sha256: identity, atlas_snapshot_id: outer.atlas_snapshot_id,
                atlas_sha256: outer.atlas_sha256, source_sha256: outer.source_sha256,
                parser_response_sha256: outer.parser_response_sha256, parser_text_sha256: parserHash,
                expected_host_text_sha256: sourceHostHash,
                bridge_policy_sha256: outer.bridge_policy_sha256, image_policy_sha256: outer.image_policy_sha256,
                compiler_input_observation: inputs.project(result, instance.summary),
                ...(metadata ? {compiler_package_metadata:{version:metadata.policy.version,authority:metadata.policy.authority,
                    role:metadata.policy.metadata_role,raw_sha256:metadata.expected.raw_sha256,
                    host_sha256:metadata.expected.host_sha256,raw_bytes:metadata.expected.bytes,
                    policy_sha256:metadata.expected.policy_sha256,source_handoff_sha256:identity,
                    read_calls:result.host_calls.filter(row=>row.operation==='readFile'
                        && row.path_sha256===sha(metadata.policy.metadata_virtual_path)).length,
                    reported_program_source:false}} : {}),
                scope: capturedConfig ? 'synthetic_source_declaration_captured_config_only' : 'synthetic_source_and_one_declaration_only' };
        },
        dispose() { disposed = true; instance.dispose(); },
    });
}

function sourceRecords(admitted,joined){
    if(!admitted.sourceInputs)return null;
    const keys=joined.version==='v11-private-react-barrel'?['entry','barrel','component']:['entry','component'];
    if(!exact(admitted.sourceInputs,keys)||!exact(admitted.binding.sources,keys))fail('two_source_snapshot_refused');
    return Object.fromEntries(Object.entries(admitted.sourceInputs).map(([key,source])=>{
        const row=admitted.binding.sources[key],owner=joined.source_roster[key];
        if(!Buffer.isBuffer(source.raw)||sha(source.raw)!==row.raw_sha256||source.raw.length!==row.bytes
            ||source.raw.toString('utf8')!==source.text||sha(source.text.replace(/^\ufeff/,''))!==row.host_sha256
            ||row.compiler_path!==owner.compiler_path)fail('two_source_snapshot_refused');
        return [key,{path:row.compiler_path,role:owner.role,raw_sha256:row.raw_sha256,host_sha256:row.host_sha256,bytes:row.bytes}];
    }));
}
module.exports = { admitDeclarationHandoff, createDeclarationExperiment, decodeOwnedDeclaration, decodeOwnedDeclarationBytes, sourceRecords };

'use strict';
// Pure metadata projection of already owned inputs and the SAME Program result.
const crypto = require('node:crypto');
const { hashObject } = require('./ts_input_image_experiment.cjs');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const isSha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
class CompilerInputRefusal extends Error { constructor(code) { super(code); this.code = code; } }
const refuse = code => { throw new CompilerInputRefusal(code); };
const byteSize = value => Buffer.byteLength(JSON.stringify(value), 'utf8');
function hostSha(raw) {
    try {
        const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw);
        return sha(text.replace(/^\ufeff/, ''));
    } catch { refuse('compiler_input_encoding_refused'); }
}

function prepareCompilerInputObservation(libraries, configRaw, context, policies, compilerMetadata = null) {
    const { joined, image } = policies, limit = Object.freeze({ ...image.limits });
    const libraryRoot = compilerMetadata === null ? '/__sage_ts_image__/compiler' : compilerMetadata.policy?.library_virtual_root;
    if (compilerMetadata !== null && (libraryRoot !== '/__sage_ts_image__/compiler/node_modules/typescript/lib'
        || !['v1-private-compiler-package-metadata','v2-private-react-compiler-metadata','v3-private-dom-compiler-metadata'].includes(compilerMetadata.policy.version)
        || compilerMetadata.policy.image_profile!==joined.image_profile
        || compilerMetadata.policy.default_enabled !== false || compilerMetadata.policy.production_enabled !== false
        || compilerMetadata.policy.semantic_lineage !== false
        || compilerMetadata.expected?.source_handoff_sha256 !== context.handoff_sha256
        || compilerMetadata.expected?.policy_sha256 !== hashObject(compilerMetadata.policy))) refuse('compiler_input_metadata_context_refused');
    const packageBytes = compilerMetadata === null ? 0 : compilerMetadata.expected?.bytes;
    if (compilerMetadata !== null && (!Buffer.isBuffer(compilerMetadata.raw)
        || !Number.isSafeInteger(packageBytes) || packageBytes < 1 || packageBytes !== compilerMetadata.raw.length
        || packageBytes > compilerMetadata.policy.max_metadata_bytes
        || compilerMetadata.expected.raw_sha256 !== sha(compilerMetadata.raw)
        || compilerMetadata.expected.host_sha256 !== hostSha(compilerMetadata.raw))) refuse('compiler_input_metadata_context_refused');
    if (image.production_enabled !== false || image.lineage_enabled !== false
        || ['compiler_files', 'compiler_file_bytes', 'compiler_bytes', 'directory_name_bytes',
            'metadata_bytes', 'auxiliary_file_bytes', 'source_file_bytes', 'host_calls']
            .some(key => !Number.isSafeInteger(limit[key]) || limit[key] <= 0))
        refuse('compiler_input_policy_refused');
    const version = joined.input_observation_version, authority = joined.input_observation_authority;
    const barrel=version==='v7-private-react-barrel-input';
    const two=barrel||version==='v6-private-react-two-source-input';
    const hasPackages=two||version==='v5-private-react-input-summary';
    const hasJsx = version === 'v4-private-tsx-input-summary';
    const hasCapturedConfig = hasPackages || hasJsx || version === 'v3-private-captured-config-summary';
    const hasDeclaration = version === 'v2-private-declaration-input-summary' || hasCapturedConfig;
    if ((!hasDeclaration && version !== 'v1-private-compiler-input-summary')
        || joined.input_observation_authority !== 'producer_observed_only'
        || joined.production_enabled !== false || joined.semantic_lineage !== false
        || !exact(context, ['source_path', 'source_host_sha256', 'source_bytes', 'handoff_sha256',
            ...(two?['sources']:[]), ...(hasDeclaration ? ['declaration'] : []), ...(hasCapturedConfig ? ['config'] : []), ...(hasJsx ? ['jsx_declaration'] : []),...(hasPackages?['typing_packages']:[])])
        || context.source_path !== joined.compiler_virtual_path
        || !isSha(context.source_host_sha256) || !isSha(context.handoff_sha256)
        || !Number.isSafeInteger(context.source_bytes) || context.source_bytes < 0
        || context.source_bytes > limit.source_file_bytes)
        refuse('compiler_input_context_refused');
    const projectSources=new Map();
    if(two){
        if(!exact(context.sources,barrel?['entry','barrel','component']:['entry','component']))refuse('compiler_input_sources_refused');
        let total=0;
        for(const [key,row] of Object.entries(context.sources)){
            const owner=joined.source_roster[key];
            if(!exact(row,['path','role','raw_sha256','host_sha256','bytes'])||row.path!==owner.compiler_path||row.role!==owner.role
                ||!isSha(row.raw_sha256)||!isSha(row.host_sha256)||!Number.isSafeInteger(row.bytes)||row.bytes<0
                ||row.bytes>limit.source_file_bytes||projectSources.has(sha(row.path)))refuse('compiler_input_sources_refused');
            projectSources.set(sha(row.path),{key,...row});total+=row.bytes;
        }
        const entry=context.sources.entry;
        if(entry.path!==context.source_path||entry.host_sha256!==context.source_host_sha256||entry.bytes!==context.source_bytes
            ||total>joined.max_total_source_bytes)refuse('compiler_input_sources_refused');
    }else projectSources.set(sha(context.source_path),{host_sha256:context.source_host_sha256,bytes:context.source_bytes});
    if (!Buffer.isBuffer(configRaw) || configRaw.length > limit.auxiliary_file_bytes)
        refuse('compiler_input_config_refused');
    if (hasCapturedConfig && (!exact(context.config, ['path', 'atlas_context_sha256'])
        || context.config.path !== joined.config_virtual_path || context.config.path !== '/__sage_ts_image__/project/tsconfig.json'
        || !isSha(context.config.atlas_context_sha256) || context.config.atlas_context_sha256 !== context.declaration?.atlas_context_sha256
        || configRaw.length > joined.max_config_bytes)) refuse('compiler_input_config_refused');
    const config = Object.freeze({ owner: hasCapturedConfig ? 'owned_synthetic_atlas_auxiliary' : 'joined_adapter_generated',
        target_config: false, raw_sha256: sha(configRaw), host_text_sha256: hostSha(configRaw), raw_bytes: configRaw.length,
        ...(hasCapturedConfig ? { atlas_context_sha256: context.config.atlas_context_sha256 } : {}) });
    let declarationMetadata = null, declarationPath = '';
    if (hasDeclaration) {
        const declaration = context.declaration;
        if (joined.max_auxiliary_files !== (hasJsx ? 3 : hasCapturedConfig ? 2 : 1)
            || !exact(declaration, ['path', 'raw', 'atlas_context_sha256'])
            || declaration.path !== joined.auxiliary_virtual_path
            || declaration.path !== '/__sage_ts_image__/project/types.d.ts'
            || !Buffer.isBuffer(declaration.raw)
            || declaration.raw.length > Math.min(joined.max_auxiliary_bytes, limit.auxiliary_file_bytes)
            || !isSha(declaration.atlas_context_sha256)) refuse('compiler_input_declaration_refused');
        declarationPath = sha(declaration.path);
        declarationMetadata = Object.freeze({ owner: 'owned_atlas_auxiliary_input',
            raw_sha256: sha(declaration.raw), host_text_sha256: hostSha(declaration.raw),
            raw_bytes: declaration.raw.length, atlas_context_sha256: declaration.atlas_context_sha256 });
    }
    let jsxMetadata=null,jsxPath='';
    if(hasJsx){
        const jsx=context.jsx_declaration;
        if(!exact(jsx,['path','raw','atlas_context_sha256'])||jsx.path!==joined.jsx_virtual_path
            ||jsx.path!=='/__sage_ts_image__/project/jsx.d.ts'||!Buffer.isBuffer(jsx.raw)
            ||jsx.raw.length>Math.min(joined.max_jsx_bytes,limit.auxiliary_file_bytes)
            ||jsx.atlas_context_sha256!==context.declaration.atlas_context_sha256)refuse('compiler_input_jsx_refused');
        jsxPath=sha(jsx.path);
        jsxMetadata=Object.freeze({owner:'owned_synthetic_jsx_declaration',contract_kind:'synthetic_jsx_contract_not_react_typings',
            raw_sha256:sha(jsx.raw),host_text_sha256:hostSha(jsx.raw),raw_bytes:jsx.raw.length,atlas_context_sha256:jsx.atlas_context_sha256});
    }
    const declarations=new Map([...(hasDeclaration?[[declarationPath,declarationMetadata]]:[]),...(hasJsx?[[jsxPath,jsxMetadata]]:[])]);
    const declarationBytes = (declarationMetadata?.raw_bytes || 0)+(jsxMetadata?.raw_bytes || 0);
    const packageEntries=hasPackages?context.typing_packages?.entries:[];
    if(hasPackages&&(!Array.isArray(packageEntries)||hashObject(context.typing_packages.policy)!==image.package_policy_sha256
        ||packageEntries.some(row=>!Buffer.isBuffer(row.raw)||sha(row.raw)!==row.raw_sha256)))refuse('compiler_input_typing_context');
    const typingByPath=new Map(packageEntries.map(row=>[sha(row.path),row]));
    const typingBytes=packageEntries.reduce((n,row)=>n+row.raw.length,0);
    if (!Array.isArray(libraries) || !libraries.length || libraries.length > limit.compiler_files)
        refuse('compiler_input_inventory_refused');
    const metadata = [], folded = new Set(), byDigest = new Map();
    let rawBytes = 0, pathBytes = 0, metadataBytes = 2;
    for (const entry of libraries) {
        if (!exact(entry, ['path', 'role', 'raw']) || entry.role !== 'compiler'
            || typeof entry.path !== 'string'
            || !entry.path.startsWith(libraryRoot+'/')
            || !/^lib[a-zA-Z0-9_.-]*\.d\.ts$/.test(entry.path.slice(libraryRoot.length+1))
            || entry.path.includes('..') || folded.has(entry.path.toLowerCase()) || !Buffer.isBuffer(entry.raw)
            || entry.raw.length > limit.compiler_file_bytes)
            refuse('compiler_input_entry_refused');
        rawBytes += entry.raw.length;
        pathBytes += Buffer.byteLength(entry.path, 'utf8');
        if (rawBytes > limit.compiler_bytes || pathBytes > limit.directory_name_bytes)
            refuse('compiler_input_budget_refused');
        folded.add(entry.path.toLowerCase());
        const row = Object.freeze({ path: entry.path, path_sha256: sha(entry.path),
            raw_sha256: sha(entry.raw), host_text_sha256: hostSha(entry.raw), raw_bytes: entry.raw.length });
        metadataBytes += byteSize(row) + 1;
        if (metadataBytes > limit.metadata_bytes) refuse('compiler_input_budget_refused');
        metadata.push(row); byDigest.set(row.path_sha256, row);
    }
    metadata.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
    const manifest = rows => rows.map(row => ({ path: row.path, raw_sha256: row.raw_sha256,
        host_text_sha256: row.host_text_sha256, raw_bytes: row.raw_bytes }));
    const capturedIdentity = hashObject(manifest(metadata));
    const expectedSource = sha(context.source_path), expectedSourceText = context.source_host_sha256;
    const handoffIdentity = context.handoff_sha256, sourceBytes = [...projectSources.values()].reduce((n,row)=>n+row.bytes,0);
    const expectedImagePolicy = hashObject(image);
    return Object.freeze({
        project(result, snapshot) {
            if (!result || result.status !== 'DIAGNOSTIC_EXPERIMENT_NOT_ADMITTED'
                || result.production_enabled !== false || result.semantic_lineage !== false
                || result.native_project_equivalent !== false || !snapshot
                || !isSha(result.image_sha256) || result.image_sha256 !== snapshot.image_sha256
                || !isSha(result.envelope_sha256) || result.envelope_sha256 !== snapshot.envelope_sha256
                || snapshot.file_count !== metadata.length + 1 + projectSources.size + declarations.size + packageEntries.length + (compilerMetadata === null ? 0 : 1)
                || snapshot.content_bytes !== rawBytes + config.raw_bytes + sourceBytes + declarationBytes + packageBytes + typingBytes
                || result.metrics?.content_bytes !== snapshot.content_bytes)
                refuse('compiler_input_result_refused');
            if (!Array.isArray(result.source_files) || !result.source_files.length
                || result.source_files.length > metadata.length + projectSources.size + declarations.size + packageEntries.length
                || !Array.isArray(result.host_calls) || result.host_calls.length > limit.host_calls
                || byteSize(result.source_files) + byteSize(result.host_calls) > limit.metadata_bytes)
                refuse('compiler_input_program_refused');
            const reads = new Set(), seen = new Set(), used = [];
            for (const call of result.host_calls) {
                if (!exact(call, ['operation', 'path_sha256']) || typeof call.operation !== 'string'
                    || !call.operation || !isSha(call.path_sha256)) refuse('compiler_input_host_call_refused');
                if (call.operation === 'readFile') reads.add(call.path_sha256);
            }
            const sourceSeen = new Set();
            const declarationSeen=new Set(),typingUsed=[];
            for (const row of result.source_files) {
                if (!exact(row, ['path_sha256', 'text_sha256']) || !isSha(row.path_sha256)
                    || !isSha(row.text_sha256) || seen.has(row.path_sha256))
                    refuse('compiler_input_program_refused');
                seen.add(row.path_sha256);
                if (projectSources.has(row.path_sha256)) {
                    if (row.text_sha256 !== projectSources.get(row.path_sha256).host_sha256) refuse('compiler_input_source_mismatch');
                    if(two&&!reads.has(row.path_sha256))refuse('compiler_input_source_read_missing');
                    sourceSeen.add(row.path_sha256);
                } else if (declarations.has(row.path_sha256)) {
                    if (row.text_sha256 !== declarations.get(row.path_sha256).host_text_sha256 || !reads.has(row.path_sha256))
                        refuse('compiler_input_declaration_unmatched');
                    declarationSeen.add(row.path_sha256);
                } else if(typingByPath.has(row.path_sha256)){
                    const entry=typingByPath.get(row.path_sha256);
                    if(entry.kind!=='declaration'||entry.host_sha256!==row.text_sha256||!reads.has(row.path_sha256))refuse('compiler_input_typing_unmatched');
                    typingUsed.push(entry);
                } else {
                    const owned = byDigest.get(row.path_sha256);
                    if (!owned || row.text_sha256 !== owned.host_text_sha256 || !reads.has(row.path_sha256))
                        refuse('compiler_input_library_unmatched');
                    used.push(owned);
                }
            }
            if (sourceSeen.size!==projectSources.size || declarationSeen.size!==declarations.size || !used.length) refuse('compiler_input_program_missing');
            if(hasPackages&&context.typing_packages.policy.required_program_paths.some(path=>!typingUsed.some(row=>row.path==='/__sage_ts_image__/project/'+path)))refuse('compiler_input_typing_missing');
            used.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
            const usedBytes = used.reduce((count, row) => count + row.raw_bytes, 0);
            const observation = {
                version, authority,
                status: 'matched_reported_program', image_sha256: result.image_sha256,
                handoff_sha256: handoffIdentity, image_policy_sha256: expectedImagePolicy,
                config,
                ...(two?{project_sources:Object.fromEntries([...projectSources.values()].map(row=>[row.key,{
                    role:row.role,raw_sha256:row.raw_sha256,host_text_sha256:row.host_sha256,raw_bytes:row.bytes,
                    reported_program_seen:true,positive_read_matched:true}]))}:{}),
                ...(hasDeclaration ? { project_declaration: { ...declarationMetadata,
                    reported_program_seen: true, positive_read_matched: true } } : {}),
                ...(hasJsx ? {synthetic_jsx_contract:{...jsxMetadata,reported_program_seen:true,positive_read_matched:true}} : {}),
                ...(hasPackages?{typing_packages:{inventory_sha256:context.typing_packages.policy.inventory_sha256,
                    captured_count:packageEntries.length,captured_raw_bytes:typingBytes,reported_program_count:typingUsed.length,
                    positive_read_matched_count:typingUsed.length,
                    reported_program_manifest_sha256:hashObject(typingUsed.map(row=>({path_sha256:sha(row.path),host_sha256:row.host_sha256})).sort((a,b)=>a.path_sha256.localeCompare(b.path_sha256)))}}:{}),
                compiler_libraries: { captured_count: metadata.length, captured_raw_bytes: rawBytes,
                    captured_manifest_sha256: capturedIdentity, reported_program_used_count: used.length,
                    reported_program_used_raw_bytes: usedBytes, positive_read_matched_count: used.length,
                    captured_not_reported_used_count: metadata.length - used.length,
                    captured_not_reported_used_raw_bytes: rawBytes - usedBytes,
                    reported_used_manifest_sha256: hashObject(manifest(used)) },
                program_completeness_established: false, semantic_lineage: false, production_enabled: false,
            };
            if (byteSize(observation) > limit.metadata_bytes) refuse('compiler_input_budget_refused');
            return observation;
        },
    });
}

module.exports = { prepareCompilerInputObservation, CompilerInputRefusal };

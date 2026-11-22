'use strict';
// Private controlled caller. Never a product CLI or real-repository adapter.
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const root = path.resolve(__dirname, '../../..');
const profile = JSON.parse(fs.readFileSync(path.join(root, 'config/analysis_snapshot_lineage_contract.json'), 'utf8'))
    .artifacts.ts_diagnostics;
const barrelMode=String(process.argv[2]||'').startsWith('react-barrel:');
const twoMode=barrelMode||String(process.argv[2]||'').startsWith('react-two:');
const locationMode=twoMode||String(process.argv[2]||'').startsWith('react-dom-locations:');
const domMode = locationMode || String(process.argv[2] || '').startsWith('react-dom:');
const reactMode = domMode || String(process.argv[2] || '').startsWith('react:');
const tsxMode = reactMode || String(process.argv[2] || '').startsWith('tsx:');
const objectMode = tsxMode || String(process.argv[2] || '').startsWith('object:');
const importMode = objectMode || String(process.argv[2] || '').startsWith('import:');
const configMode = importMode || String(process.argv[2] || '').startsWith('config:');
const declarationMode = configMode || String(process.argv[2] || '').startsWith('declaration:');
const joined = barrelMode ? profile.private_owned_react_barrel_handoff : twoMode ? profile.private_owned_react_two_source_handoff : locationMode ? profile.private_owned_react_dom_locations_handoff : domMode ? profile.private_owned_react_dom_handoff : reactMode ? profile.private_owned_react_handoff : tsxMode ? profile.private_owned_tsx_handoff : objectMode ? profile.private_owned_object_type_handoff
    : importMode ? profile.private_owned_type_import_handoff : configMode ? profile.private_owned_config_handoff
    : declarationMode ? profile.private_owned_declaration_handoff : profile.private_owned_image_handoff;
const policies = { joined, bridge: profile[joined.bridge_profile], image: profile[joined.image_profile],
    ...(importMode ? { capture: profile[joined.capture_profile] } : {}),...(reactMode?{packages:profile[joined.package_profile]}:{}),
    ...(barrelMode?{bridges:Object.fromEntries(Object.values(joined.source_roster).map(row=>[row.bridge_profile,profile[row.bridge_profile]]))}:{}) };
const { admitHandoff, createJoinedExperiment, canonical } = require('../../core/owned_image_handoff.cjs');
const declarationApi = importMode ? require('../../core/owned_type_import_handoff.cjs') : configMode ? require('../../core/owned_config_handoff.cjs')
    : declarationMode ? require('../../core/owned_declaration_handoff.cjs') : null;
const control = barrelMode ? process.argv[2].slice('react-barrel:'.length) : twoMode ? process.argv[2].slice('react-two:'.length) : locationMode ? process.argv[2].slice('react-dom-locations:'.length) : domMode ? process.argv[2].slice('react-dom:'.length) : reactMode ? process.argv[2].slice('react:'.length) : tsxMode ? process.argv[2].slice('tsx:'.length) : configMode ? process.argv[2].slice('config:'.length)
    : declarationMode ? process.argv[2].slice('declaration:'.length) : process.argv[2];
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
if(control==='calibration-metadata-correspondence'||control==='calibration-metadata-projection'||control.startsWith('calibration-metadata-projection-unit-')){
    const policy=profile.private_owned_diagnostic_projection;
    policies.diagnosticProjection={policy,input:profile[policy.input_profile]};
}
if(control==='calibration-metadata-correspondence'){
    policies.diagnosticCorrespondence={policy:profile.private_owned_diagnostic_correspondence,projection:profile.private_owned_diagnostic_projection};
}
let instance, output, fallbackAttempts = 0, compilerInvocations = 0, disposedRefused = false, cachedResolutionReads = 0;
const restore = [];
const fail = code => { const error = new Error(code); error.code = code; throw error; };
function poison(owner, key) {
    if (typeof owner[key] !== 'function') return;
    const original = owner[key];
    owner[key] = () => { fallbackAttempts++; fail('joined_live_fallback_refused'); };
    restore.push(() => { owner[key] = original; });
}
function readRequest() {
    const cap = declarationMode ? joined.max_request_bytes : policies.bridge.max_request_bytes, pieces = [];
    let size = 0, ended = false;
    while (!ended) {
        const buffer = Buffer.alloc(Math.min(65536, cap - size + 1));
        const n = fs.readSync(0, buffer, 0, buffer.length, null);
        if (!n) fail('joined_frame_incomplete');
        const chunk = buffer.subarray(0, n), newline = chunk.indexOf(10);
        const piece = newline < 0 ? chunk : chunk.subarray(0, newline);
        size += piece.length;
        if (size + 1 > cap) fail('joined_frame_over_budget');
        pieces.push(piece);
        if (newline >= 0) {
            if (newline !== chunk.length - 1) fail('joined_multiple_frames_refused');
            ended = true;
        }
    }
    let raw, value;
    try { raw = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(Buffer.concat(pieces, size)); value = JSON.parse(raw); }
    catch { fail('joined_frame_invalid'); }
    if (canonical(value) !== raw || !value || typeof value !== 'object' || Array.isArray(value)
        || Object.keys(value).sort().join(',') !== 'expected,request') fail('joined_frame_invalid');
    return value;
}
function libraryEntries(modulePath) {
    const limit = policies.image.limits, directory = path.dirname(modulePath);
    const all = fs.readdirSync(directory);
    if (all.length > limit.directory_entries
        || all.reduce((n, name) => n + Buffer.byteLength(name, 'utf8'), 0) > limit.directory_name_bytes)
        fail('joined_library_inventory_budget');
    const names = all.filter(name => /^lib[^/]*\.d\.ts$/.test(name)).sort();
    if (!names.length || names.length > limit.compiler_files) fail('joined_library_inventory_budget');
    let total = 0;
    return names.map(name => {
        const candidate = path.join(directory, name);
        if (fs.realpathSync(candidate) !== candidate) fail('joined_library_alias_refused');
        const cap = Math.min(limit.compiler_file_bytes, limit.compiler_bytes - total);
        const fd = fs.openSync(candidate, 'r');
        let raw;
        try {
            const count = fs.fstatSync(fd).size;
            if (count > cap) fail('joined_library_bytes_budget');
            const buffer = Buffer.alloc(count + 1);
            let size = 0, n;
            while (size < buffer.length && (n = fs.readSync(fd, buffer, size, buffer.length - size, null))) size += n;
            if (size !== count) fail('joined_library_changed');
            raw = buffer.subarray(0, size);
        } finally { fs.closeSync(fd); }
        total += raw.length;
        return { path: '/__sage_ts_image__/compiler/' + name, role: 'compiler', raw };
    });
}
try {
    const envelope = readRequest();
    const modulePath = require.resolve('typescript', { paths: [path.join(root, 'tools/engines')] });
    const reference = sha(fs.readFileSync(modulePath));
    const ts = require(modulePath);
    let admitted = declarationMode
        ? declarationApi.admitDeclarationHandoff(ts, envelope.request, envelope.expected, policies, reference)
        : admitHandoff(ts, envelope.request, envelope.expected, policies, reference);
    if(reactMode&&control==='admission'){
        process.stdout.write(JSON.stringify({status:'PRIVATE_REACT_ADMISSION_ONLY',compiler_invocations:0,cached_resolution_reads:0,
            package_files:admitted.typingPackages.entries.length,package_raw_bytes:admitted.typingPackages.raw_bytes,
            package_inventory_sha256:admitted.typingPackages.policy.inventory_sha256,raw_image_persisted:false}));
        process.exit(0);
    }
    if(locationMode&&control.startsWith('location-unit-')){
        const unit=require('./owned_context_calibration.cjs').runLocationControl;
        try{
            for(const key of ['readFileSync','openSync','statSync','lstatSync','readdirSync','existsSync','realpathSync'])poison(fs,key);
            for(const key of ['readFile','fileExists','directoryExists','getDirectories','readDirectory','realpath'])poison(ts.sys,key);
            output=unit(ts,admitted,policies,control.slice('location-unit-'.length));
        }finally{for(const undo of restore.reverse())undo();restore.length=0;}
        Object.assign(output,{fallback_attempts:fallbackAttempts,compiler_invocations:0,cached_resolution_reads:0});
        const serialized=JSON.stringify(output);
        if(Buffer.byteLength(serialized)>Math.min(policies.bridge.max_response_bytes,policies.image.limits.metadata_bytes))fail('joined_result_over_budget');
        process.stdout.write(serialized);process.exit(0);
    }
    if(tsxMode && !control.startsWith('calibration-metadata')) fail('tsx_calibration_required');
    if (process.argv[2] === 'mutate_policy') policies.joined.compiler_virtual_path = '/changed/entry.ts';
    if (process.argv[2] === 'mutate_image') policies.image.compiler_options.noEmit = false;
    if (process.argv[2] === 'mutate_source') admitted.raw.fill(0);
    if (process.argv[2] === 'mutate_text') admitted = { ...admitted, text: 'changed' };
    if (control === 'mutate_declaration') admitted.auxiliaryRaw.fill(0);
    if (control === 'mutate_config') admitted.configRaw.fill(0);
    const libraries = libraryEntries(modulePath);
    const api = Object.create(ts);
    Object.defineProperty(api, 'createProgram', { value: options => {
        compilerInvocations++;
        if (control === 'host_throw') throw new Error('private fixture compiler failure');
        const program = ts.createProgram(options);
        if (!importMode) return program;
        const observed = Object.create(program);
        Object.defineProperty(observed, 'getResolvedModuleFromModuleSpecifier', { value: control === 'api_missing' ? undefined : (...args) => {
            cachedResolutionReads++;
            const resolved = program.getResolvedModuleFromModuleSpecifier(...args);
            if (control === 'missing_resolution') return { resolvedModule: undefined };
            if (control === 'wrong_resolution') return { ...resolved, resolvedModule: { ...resolved.resolvedModule,
                resolvedFileName: '/__sage_ts_image__/project/other.d.ts' } };
            if (control === 'external_resolution') return { ...resolved, resolvedModule: { ...resolved.resolvedModule,
                isExternalLibraryImport: true } };
            return resolved;
        } });
        if (control === 'wrong_program_source' || control === 'wrong_program_target')
            Object.defineProperty(observed, 'getSourceFile', { value: name => {
                const file = program.getSourceFile(name);
                const selected = control === 'wrong_program_source' ? joined.compiler_virtual_path : joined.auxiliary_virtual_path;
                return name === selected ? { ...file, text: 'changed' } : file;
            } });
        return observed;
    } });
    if (control.startsWith('calibration')) {
        output = require('./owned_context_calibration.cjs').runCalibration(api, admitted, policies, libraries, control,
            control.startsWith('calibration-metadata') ? profile[joined.compiler_metadata_profile||'private_owned_compiler_metadata_calibration'] : null);
    } else {
    instance = declarationMode
        ? declarationApi.createDeclarationExperiment(api, admitted, policies, libraries)
        : createJoinedExperiment(api, admitted, policies, libraries);
    if (process.argv[2] === 'mutate_buffer') admitted.raw.fill(0);
    if (control === 'mutate_declaration_after') admitted.auxiliaryRaw.fill(0);
    if (control === 'mutate_config_after') admitted.configRaw.fill(0);
    for (const key of ['readFileSync', 'statSync', 'readdirSync', 'existsSync', 'realpathSync', 'openSync']) poison(fs, key);
    for (const key of ['readFile', 'fileExists', 'directoryExists', 'readDirectory', 'getDirectories', 'realpath']) poison(ts.sys, key);
    if (control === 'poison_probe') fs.readFileSync('/fixture-only-observer-probe');
    const expected = JSON.parse(JSON.stringify(envelope.expected));
    if (control === 'wrong_expected') expected.invocation_id = 'changed';
    if (control === 'disposed') instance.dispose();
    output = instance.replay(expected);
    if (control === 'second_program') output = instance.replay(expected);
    }
} catch (error) {
    const code = typeof error.code === 'string' && /^[a-z_]+$/.test(error.code) ? error.code : 'joined_unexpected_error';
    output = { status: 'REFUSED', code, raw_image_persisted: false };
    if (control.startsWith('calibration')) output.temporary_copy_removed = error.calibration_cleanup ?? null;
} finally {
    for (const undo of restore.reverse()) undo();
    if (instance) {
        instance.dispose();
        try { instance.replay({}); } catch (error) {
            disposedRefused = error.code === (declarationMode ? 'declaration_disposed' : 'joined_disposed');
        }
    }
}
output.fallback_attempts = fallbackAttempts;
output.compiler_invocations = compilerInvocations;
output.disposed_refused = disposedRefused;
if (importMode) output.cached_resolution_reads = cachedResolutionReads;
let serialized = JSON.stringify(output);
if (Buffer.byteLength(serialized, 'utf8') > Math.min(policies.bridge.max_response_bytes, policies.image.limits.metadata_bytes))
    serialized = JSON.stringify({ status: 'REFUSED', code: 'joined_result_over_budget', raw_image_persisted: false });
process.stdout.write(serialized);

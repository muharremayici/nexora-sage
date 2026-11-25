'use strict';
// Private compiler-host service; caller-owned synthetic fixture only, no engine activation.
const crypto = require('node:crypto');
const posix = require('node:path').posix;
const { performance } = require('node:perf_hooks');
const ROOT = '/__sage_ts_image__';
const PROJECT = ROOT + '/project';
const COMPILER = ROOT + '/compiler';
const digest = value => crypto.createHash('sha256').update(value).digest('hex');
const canonical = value => Array.isArray(value) ? '[' + value.map(canonical).join(',') + ']'
    : value && typeof value === 'object' ? '{' + Object.keys(value).sort()
        .map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}' : JSON.stringify(value);
const hashObject = value => digest(canonical(value));
const bytes = value => Buffer.byteLength(canonical(value), 'utf8');
class ImageRefusal extends Error { constructor(code) { super(code); this.code = code; } }
const refuse = code => { throw new ImageRefusal(code); };
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
    && canonical(Object.keys(value).sort()) === canonical(keys.slice().sort());
const sha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

// Private two-phase owner. Kind checks stay between decoding and hashing in the
// image loop. Neither phase loads a compiler, policy, filesystem or telemetry.
const ownedTextPhases = new WeakMap();
function _ownedTextPhase() {
    const phase = Object.freeze({
        dispose() { ownedTextPhases.delete(phase); },
        toJSON() { throw new TypeError('owned_text_phase_not_serializable'); },
        [Symbol.for('nodejs.util.inspect.custom')]() { return '<private owned text phase>'; }
    });
    return phase;
}

function _copyDecodeOwnedText(input) {
    if (!Buffer.isBuffer(input)) refuse('entry_invalid');
    const raw = Buffer.from(input); // Never retain caller-mutable views.
    let parserText;
    try { parserText = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw); }
    catch { refuse('encoding_refused'); }
    const hostText = parserText.startsWith('\ufeff') ? parserText.slice(1) : parserText;
    const phase = _ownedTextPhase();
    ownedTextPhases.set(phase, { raw, parserText, hostText });
    return phase;
}

function _finishOwnedText(phase) {
    const held = ownedTextPhases.get(phase);
    if (!held) refuse('entry_invalid');
    try {
        return { raw_sha256: digest(held.raw), parser_sha256: digest(held.parserText),
            host_sha256: digest(held.hostText), size_bytes: held.raw.length, hostText: held.hostText };
    } finally { ownedTextPhases.delete(phase); }
}

function policyFor(input) {
    const dom=input?.version==='v3-private-react-dom-image';
    const packaged = dom || input?.version === 'v2-private-react-package-image';
    if (!exact(input, ['version', 'schema', 'default_enabled', 'production_enabled', 'lineage_enabled',
        'profile', 'compiler_options', 'limits', 'claim_boundary', ...(packaged ? ['package_policy_sha256'] : []),...(dom?['dom_library']:[])])
        || input.version !== (dom?'v3-private-react-dom-image':packaged ? 'v2-private-react-package-image' : 'v1-synthetic-memory-only')
        || input.profile !== (dom?'synthetic_project_with_frozen_react_dom':packaged ? 'synthetic_project_with_frozen_react_packages' : 'synthetic_single_project_explicit_files')
        || input.default_enabled !== false || input.production_enabled !== false || input.lineage_enabled !== false
        || input.schema !== (dom?'config/schemas/owned_react_dom_image.schema.json':packaged ? 'config/schemas/owned_react_image.schema.json' : 'config/schemas/typescript_input_image_experiment.schema.json')
        || packaged && !sha(input.package_policy_sha256)
        || typeof input.claim_boundary !== 'string' || !input.claim_boundary) refuse('policy_invalid');
    const limits = ['source_files', 'auxiliary_files', 'compiler_files', 'source_bytes', 'auxiliary_bytes',
        'compiler_bytes', 'source_file_bytes', 'auxiliary_file_bytes', 'compiler_file_bytes', 'directories',
        'directory_entries', 'directory_name_bytes', 'host_calls', 'metadata_bytes', 'image_bytes',
        'capture_ms', 'replay_ms', 'rss_observation_bytes'];
    if (!exact(input.limits, limits) || limits.some(k => !Number.isSafeInteger(input.limits[k]) || input.limits[k] <= 0)
        || !exact(input.compiler_options, ['target', 'module', 'moduleResolution', 'strict', 'noEmit',
            'skipLibCheck', 'types', 'lib', 'jsx']) || input.compiler_options.noEmit !== true
        || input.compiler_options.strict !== true || input.compiler_options.skipLibCheck !== false
        || !Array.isArray(input.compiler_options.types) || input.compiler_options.types.length
        || !Array.isArray(input.compiler_options.lib) || input.compiler_options.lib.length !== (dom?2:1)
        || dom&&canonical(input.compiler_options.lib)!==canonical(['ES2020','DOM'])) refuse('policy_invalid');
    if(dom&&(!exact(input.dom_library,['virtual_path','raw_sha256','host_sha256','bytes'])
        ||input.dom_library.virtual_path!==COMPILER+'/node_modules/typescript/lib/lib.dom.d.ts'
        ||!sha(input.dom_library.raw_sha256)||!sha(input.dom_library.host_sha256)
        ||!Number.isSafeInteger(input.dom_library.bytes)||input.dom_library.bytes<1))refuse('dom_library_policy_refused');
    // The owner validates the full schema before invocation. Keep the consumed
    // profile in the image identity; no local default/upper-limit substitution.
    return JSON.parse(canonical(input));
}

function compilerMetadataPolicyFor(policy) {
    const dom=policy?.version==='v3-private-dom-compiler-metadata';
    const react = dom || policy?.version === 'v2-private-react-compiler-metadata';
    if (!exact(policy, ['version','schema','default_enabled','production_enabled','semantic_lineage','image_profile',
        'metadata_virtual_path','library_virtual_root','metadata_role','max_metadata_files','max_metadata_bytes','authority','claim_boundary'])
        || policy.version !== (dom?'v3-private-dom-compiler-metadata':react ? 'v2-private-react-compiler-metadata' : 'v1-private-compiler-package-metadata')
        || policy.schema !== (dom?'config/schemas/owned_react_dom_metadata.schema.json':react ? 'config/schemas/owned_react_metadata.schema.json' : 'config/schemas/owned_compiler_metadata_calibration.schema.json')
        || policy.default_enabled !== false || policy.production_enabled !== false || policy.semantic_lineage !== false
        || policy.image_profile !== (dom?'private_react_dom_image':react ? 'private_react_input_image' : 'private_input_image_experiment') || policy.authority !== 'producer_observed_only'
        || policy.metadata_virtual_path !== COMPILER + '/node_modules/typescript/package.json'
        || policy.library_virtual_root !== COMPILER + '/node_modules/typescript/lib'
        || policy.metadata_role !== 'compiler_package_metadata' || policy.max_metadata_files !== 1
        || policy.max_metadata_bytes !== 65536 || typeof policy.claim_boundary !== 'string' || !policy.claim_boundary)
        refuse('compiler_metadata_policy_refused');
    return JSON.parse(canonical(policy));
}

function admitCompilerPackageMetadata(ts, input, owner) {
    if (!ts || typeof ts.version !== 'string' || typeof ts.parseJsonText !== 'function') refuse('compiler_metadata_toolchain_refused');
    if (!exact(input, ['policy', 'expected', 'raw']) || !Buffer.isBuffer(input.raw)) refuse('compiler_metadata_input_refused');
    const policy = compilerMetadataPolicyFor(input.policy), expected = input.expected;
    if (!exact(expected, ['raw_sha256','host_sha256','bytes','policy_sha256','source_handoff_sha256',
        'compiler_version','compiler_source_sha256'])
        || !sha(expected.raw_sha256) || !sha(expected.host_sha256) || !sha(expected.policy_sha256)
        || !sha(expected.source_handoff_sha256) || !sha(expected.compiler_source_sha256)
        || expected.policy_sha256 !== hashObject(policy) || expected.source_handoff_sha256 !== owner.source_handoff_sha256
        || expected.compiler_version !== ts.version || expected.compiler_version !== owner.compiler_version
        || expected.compiler_source_sha256 !== owner.compiler_source_sha256
        || !Number.isSafeInteger(expected.bytes) || expected.bytes < 1 || expected.bytes > policy.max_metadata_bytes
        || input.raw.length !== expected.bytes || digest(input.raw) !== expected.raw_sha256)
        refuse('compiler_metadata_identity_refused');
    let text, parsed;
    try {
        text = new TextDecoder('utf-8', {fatal:true, ignoreBOM:true}).decode(input.raw).replace(/^\ufeff/,'');
        parsed = JSON.parse(text);
    } catch { refuse('compiler_metadata_encoding_or_json_refused'); }
    if (digest(text) !== expected.host_sha256 || !parsed || Array.isArray(parsed)
        || parsed.name !== 'typescript' || parsed.version !== ts.version) refuse('compiler_metadata_package_refused');
    const tree = ts.parseJsonText(policy.metadata_virtual_path, text), pending = [tree];
    if (tree.parseDiagnostics.length) refuse('compiler_metadata_json_refused');
    while (pending.length) {
        const node = pending.pop();
        if (ts.isObjectLiteralExpression(node)) {
            const names = new Set();
            for (const property of node.properties) {
                if (!ts.isPropertyAssignment(property) || !ts.isStringLiteral(property.name)
                    || names.has(property.name.text)) refuse('compiler_metadata_duplicate_key');
                names.add(property.name.text);
            }
        }
        ts.forEachChild(node, child => {pending.push(child);});
    }
    return Object.freeze({policy:JSON.parse(canonical(policy)),expected:JSON.parse(canonical(expected)),raw:Buffer.from(input.raw)});
}

function admitTypingPackages(wire, policy) {
    if (!exact(policy,['version','schema','default_enabled','production_enabled','semantic_lineage','pins',
        'inventory_sha256','max_files','max_bytes','max_file_bytes','required_program_paths','claim_boundary'])
        || policy.version !== 'v1-private-frozen-react-packages' || policy.schema !== 'config/schemas/owned_react_packages.schema.json'
        || policy.default_enabled !== false || policy.production_enabled !== false || policy.semantic_lineage !== false
        || !sha(policy.inventory_sha256) || !policy.pins || Array.isArray(policy.pins)
        || ['max_files','max_bytes','max_file_bytes'].some(k=>!Number.isSafeInteger(policy[k])||policy[k]<1)
        || !Array.isArray(policy.required_program_paths) || !policy.required_program_paths.length
        || !exact(wire,['pins','files']) || canonical(wire.pins)!==canonical(policy.pins)
        || !Array.isArray(wire.files) || wire.files.length!==policy.max_files) refuse('typing_package_policy_or_roster');
    const manifest=[], entries=[], seen=new Set();let total=0;
    for(const item of wire.files){
        if(!exact(item,['path','kind','bytes','raw_sha256','host_text_sha256','role','executable_mode_bits','package','virtual_path','utf8Base64'])
            || item.kind!=='regular'||item.executable_mode_bits!==false||!Object.hasOwn(policy.pins,item.package)
            || typeof item.path!=='string'||!/^[A-Za-z0-9_.\/-]+$/.test(item.path)
            || item.path!==posix.normalize(item.path)||item.path.startsWith('/')||item.path.split('/').some(p=>p==='..'||p==='.')
            || item.virtual_path!==PROJECT+'/node_modules/'+item.package+'/'+item.path
            || seen.has(item.virtual_path.toLowerCase())||!Number.isSafeInteger(item.bytes)||item.bytes<0||item.bytes>policy.max_file_bytes
            || !sha(item.raw_sha256)||!sha(item.host_text_sha256)||typeof item.utf8Base64!=='string'
            || item.utf8Base64.length!==Math.ceil(item.bytes/3)*4) refuse('typing_package_member_refused');
        const {utf8Base64,...row}=item,raw=Buffer.from(utf8Base64,'base64');
        let host;try{host=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(raw).replace(/^\ufeff/,'');}catch{refuse('typing_package_encoding');}
        if(raw.length!==item.bytes||raw.toString('base64')!==utf8Base64||digest(raw)!==item.raw_sha256||digest(host)!==item.host_text_sha256)
            refuse('typing_package_content_changed');
        const kind=item.path.endsWith('.d.ts')?'declaration':item.path==='package.json'?'package_manifest':/^(LICENSE|NOTICE|COPYING)/i.test(posix.basename(item.path))?'license_notice':'other';
        if(item.role!==kind||(total+=raw.length)>policy.max_bytes)refuse('typing_package_role_or_budget');
        manifest.push(row);seen.add(item.virtual_path.toLowerCase());
        entries.push(Object.freeze({path:item.virtual_path,role:'auxiliary',raw:Buffer.from(raw),
            package:item.package,member:item.path,kind,host_sha256:item.host_text_sha256,raw_sha256:item.raw_sha256}));
    }
    if(total!==policy.max_bytes||hashObject({pins:wire.pins,inventory:manifest})!==policy.inventory_sha256)
        refuse('typing_package_inventory_mismatch');
    return Object.freeze({policy:JSON.parse(canonical(policy)),manifest:JSON.parse(canonical(manifest)),entries:Object.freeze(entries),raw_bytes:total});
}

function recordManifest(records) {
    return [...records.values()].map(({ path, role, raw_sha256, parser_sha256, host_sha256, size_bytes }) =>
        ({ path, role, raw_sha256, parser_sha256, host_sha256, size_bytes })).sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
}

function buildExperiment(ts, inputs, binding, policyInput, metadataInput = null, typingInput = null, preparedInput = null) {
    const started = performance.now();
    const policy = policyFor(policyInput), limit = policy.limits;
    const packaged=['v2-private-react-package-image','v3-private-react-dom-image'].includes(policy.version);
    if(packaged!==!!typingInput||packaged&&(hashObject(typingInput.policy)!==policy.package_policy_sha256
        ||hashObject({pins:typingInput.policy.pins,inventory:typingInput.manifest})!==typingInput.policy.inventory_sha256))refuse('typing_package_context_refused');
    const packageFiles=new Map();
    if(packaged&&(!Array.isArray(typingInput.entries)||typingInput.entries.length!==typingInput.manifest.length))refuse('typing_package_snapshot_changed');
    if(packaged)for(const [index,entry] of typingInput.entries.entries()){
        const frozen=typingInput.manifest[index];
        if(packageFiles.has(entry.path)||entry.path!==frozen.virtual_path||entry.package!==frozen.package
            ||entry.member!==frozen.path||entry.kind!==frozen.role||entry.raw_sha256!==frozen.raw_sha256
            ||entry.host_sha256!==frozen.host_text_sha256||!Buffer.isBuffer(entry.raw)||entry.raw.length!==frozen.bytes
            ||digest(entry.raw)!==entry.raw_sha256)refuse('typing_package_snapshot_changed');
        packageFiles.set(entry.path,entry);
    }
    const metadata = metadataInput === null ? null : admitCompilerPackageMetadata(ts, metadataInput, {
        compiler_version:binding?.compiler_version,compiler_source_sha256:binding?.compiler_source_sha256,
        source_handoff_sha256:binding?.source_handoff_sha256});
    const compilerRoot = metadata ? metadata.policy.library_virtual_root : COMPILER;
    if (!ts || typeof ts.createProgram !== 'function' || !binding || binding.compiler_version !== ts.version
        || binding.policy_sha256 !== hashObject(policy)) refuse('toolchain_or_policy_binding');
    if (!exact(binding, ['atlas_snapshot_id', 'atlas_sha256', 'producer_run_id', 'policy_sha256',
        'compiler_version', 'compiler_source_sha256', 'source_sha256', ...(metadata ? ['compiler_metadata_sha256',
            'compiler_metadata_policy_sha256','source_handoff_sha256'] : [])]) || !sha(binding.atlas_snapshot_id) || !sha(binding.atlas_sha256)
        || !sha(binding.compiler_source_sha256)
        || typeof binding.producer_run_id !== 'string' || !binding.producer_run_id
        || !binding.source_sha256 || typeof binding.source_sha256 !== 'object') refuse('binding_invalid');
    if (metadata && (binding.compiler_metadata_sha256 !== metadata.expected.raw_sha256
        || binding.compiler_metadata_policy_sha256 !== metadata.expected.policy_sha256)) refuse('compiler_metadata_binding_refused');
    if (!exact(inputs, ['entries', 'namespace_complete']) || inputs.namespace_complete !== true
        || !Array.isArray(inputs.entries)) refuse('namespace_incomplete');
    const records = new Map(), directories = new Map(), folded = new Map();
    const totals = { source_files: 0, auxiliary_files: 0, compiler_files: 0,
        source_bytes: 0, auxiliary_bytes: 0, compiler_bytes: 0 };
    let metadataBytes = 0, contentBytes = 0, directoryEntries = 0, nameBytes = 0, disposed = false;
    const preparedMode = preparedInput !== null;
    let ingressCheck = preparedInput?.checkTime, preparedRows = null, completed = false;
    try {
    const checkTime = () => {
        if (performance.now() - started > limit.capture_ms) refuse('capture_timeout');
        ingressCheck?.();
    };
    function addDirectory(path) {
        if (directories.has(path)) return;
        if (records.has(path) || folded.has(path.toLowerCase()) && folded.get(path.toLowerCase()) !== path) refuse('path_kind_collision');
        folded.set(path.toLowerCase(), path);
        directories.set(path, { files: new Set(), directories: new Set() });
        if (directories.size > limit.directories) refuse('directory_budget');
        if (path !== '/') {
            const parent = posix.dirname(path); addDirectory(parent);
            directories.get(parent).directories.add(posix.basename(path));
        }
    }
    function strictPath(path) {
        if (typeof path !== 'string' || !path.startsWith(ROOT + '/') || !/^[a-zA-Z0-9_./-]+$/.test(path)
            || Buffer.byteLength(path, 'utf8') > limit.directory_name_bytes
            || path !== posix.normalize(path) || path.endsWith('/')
            || path.split('/').some(part => part === '.' || part === '..')) refuse('path_refused');
        return path;
    }
    addDirectory(PROJECT); addDirectory(compilerRoot);
    const countKeys = { source: 'source', auxiliary: 'auxiliary', compiler: 'compiler' };
    if (inputs.entries.length > limit.source_files + limit.auxiliary_files + limit.compiler_files) refuse('file_count_budget');
    let entries = metadata ? [...inputs.entries, {path:metadata.policy.metadata_virtual_path,
        role:'compiler_metadata',raw:metadata.raw}] : inputs.entries;
    const expectedSourceInputs = preparedInput?.source_inputs_sha256;
    if (preparedMode) {
        if (inputs.entries.some(entry => entry?.role === 'compiler')) refuse('prepared_ingress_inputs_refused');
        preparedRows = new Map();
        const compilerEntries = preparedInput.rows.map(row => {
            const entry = {path:compilerRoot+'/'+row.name,role:'compiler'};
            preparedRows.set(entry,row);return entry;
        });
        entries = [...inputs.entries,...compilerEntries];
        preparedInput = null;
    }
    for (const entry of entries) {
        checkTime();
        const preparedRow = preparedRows?.get(entry), isPrepared = preparedRow !== undefined;
        const isMetadata = metadata && entry.role === 'compiler_metadata' && entry === entries[entries.length-1];
        if (isPrepared ? !exact(entry,['path','role']) || entry.role !== 'compiler'
            : !exact(entry, ['path', 'role', 'raw']) || !Object.hasOwn(countKeys, entry.role) && !isMetadata
                || !Buffer.isBuffer(entry.raw)) refuse('entry_invalid');
        const packageEntry=packageFiles.get(entry.path);
        const path = packageEntry ? entry.path : strictPath(entry.path), role = entry.role;
        if(packageEntry&&(role!=='auxiliary'||digest(entry.raw)!==packageEntry.raw_sha256))refuse('typing_package_entry_changed');
        if (isMetadata ? path !== metadata.policy.metadata_virtual_path
            : (role === 'compiler') !== path.startsWith(compilerRoot + '/')
            || role !== 'compiler' && !path.startsWith(PROJECT + '/')) refuse('role_path_mismatch');
        if (records.has(path) || directories.has(path) || folded.has(path.toLowerCase())) refuse('path_kind_collision');
        folded.set(path.toLowerCase(), path);
        const budgetRole = isMetadata ? 'compiler' : role;
        const entryBytes = isPrepared ? preparedRow.size_bytes : entry.raw.length;
        if (entryBytes > limit[budgetRole + '_file_bytes']) refuse('file_byte_budget');
        if (++totals[budgetRole + '_files'] > limit[budgetRole + '_files']) refuse('file_count_budget');
        totals[budgetRole + '_bytes'] += entryBytes;
        if (totals[budgetRole + '_bytes'] > limit[budgetRole + '_bytes']) refuse('content_byte_budget');
        const prepared = isPrepared ? null : _copyDecodeOwnedText(entry.raw);
        let item;
        try {
            if (role === 'source' && (!/\.tsx?$/.test(path) || /\.d\.ts$/.test(path))) refuse('source_kind_refused');
            if (role === 'compiler' && !/\/lib[^/]*\.d\.ts$/.test(path)) refuse('compiler_input_refused');
            if (role === 'auxiliary' && !packageEntry && !/\/(?:tsconfig\.json|package\.json|[^/]+\.d\.ts)$/.test(path)) refuse('auxiliary_input_refused');
            item = isPrepared ? {path,role,raw_sha256:preparedRow.raw_sha256,parser_sha256:preparedRow.parser_sha256,
                host_sha256:preparedRow.host_sha256,size_bytes:preparedRow.size_bytes,hostText:preparedRow.hostText}
                : { path, role, ..._finishOwnedText(prepared) };
        } finally { prepared?.dispose(); }
        records.set(path, item); addDirectory(posix.dirname(path));
        directories.get(posix.dirname(path)).files.add(posix.basename(path));
        contentBytes += item.size_bytes;
        metadataBytes += bytes({ path, role, raw_sha256: item.raw_sha256,
            parser_sha256: item.parser_sha256, host_sha256: item.host_sha256, size_bytes: item.size_bytes });
        if (metadataBytes > limit.metadata_bytes || contentBytes + metadataBytes > limit.image_bytes) refuse('image_byte_budget');
    }
    preparedRows?.clear();preparedRows = null;
    if(packaged&&[...packageFiles.keys()].some(path=>!records.has(path)))refuse('typing_package_entry_missing');
    if(policy.dom_library){
        const entry=records.get(policy.dom_library.virtual_path),dom=policy.dom_library;
        if(!entry||entry.role!=='compiler'||entry.raw_sha256!==dom.raw_sha256||entry.host_sha256!==dom.host_sha256||entry.size_bytes!==dom.bytes)
            refuse('dom_library_snapshot_refused');
    }
    for (const [path, names] of directories) {
        const all = [...names.files, ...names.directories];
        directoryEntries += all.length;
        nameBytes += all.reduce((n, name) => n + Buffer.byteLength(name, 'utf8'), 0);
        metadataBytes += bytes({ path, files: [...names.files].sort(), directories: [...names.directories].sort() });
    }
    if (directoryEntries > limit.directory_entries || nameBytes > limit.directory_name_bytes) refuse('directory_budget');
    const sourcePaths = [...records.values()].filter(row => row.role === 'source').map(row => row.path).sort();
    if (!sourcePaths.length || canonical(sourcePaths) !== canonical(Object.keys(binding.source_sha256).sort())
        || sourcePaths.some(path => binding.source_sha256[path] !== records.get(path).parser_sha256)) refuse('atlas_text_binding');
    const preparedManifest = preparedMode ? recordManifest(records) : null;
    if (preparedMode && hashObject(preparedManifest.filter(row => row.role !== 'compiler')) !== expectedSourceInputs) refuse('prepared_ingress_inputs_refused');
    const configPath = PROJECT + '/tsconfig.json';
    const configEntry = records.get(configPath);
    if (!configEntry || configEntry.role !== 'auxiliary') refuse('config_missing');
    const parsedJson = ts.parseConfigFileTextToJson(configPath, configEntry.hostText);
    const config = parsedJson.config;
    if (parsedJson.error || !exact(config, ['files', 'compilerOptions']) || !Array.isArray(config.files) || !config.files.length
        || canonical(config.compilerOptions) !== canonical(policy.compiler_options)
        || config.files.some(name => typeof name !== 'string' || name.startsWith('/') || /[\\:]/.test(name)
            || name.split('/').some(part => part === '..' || part === '.'))
        || new Set(config.files).size !== config.files.length) refuse('config_profile_refused');
    for (const path of config.files.map(name => strictPath(PROJECT + '/' + name))) {
        const row = records.get(path);
        if (!row || !(row.role === 'source' || row.role === 'auxiliary' && path.endsWith('.d.ts'))) refuse('root_input_missing');
    }
    for (const row of records.values()) if (row.role !== 'compiler' && /\.tsx?$/.test(row.path)) {
        // The exact pinned package bytes retain their own declaration grammar.
        // Resolution still sees only this closed owned namespace and has no live fallback.
        if(packageFiles.has(row.path))continue;
        const pre = ts.preProcessFile(row.hostText, true, true);
        const local = name => (name.startsWith('./') || name.startsWith('../'))
            && posix.resolve(posix.dirname(row.path), name).startsWith(PROJECT + '/');
        if (!packaged && (pre.typeReferenceDirectives.length || pre.importedFiles.some(ref => !local(ref.fileName))
            || pre.referencedFiles.some(ref => !local(ref.fileName)))) refuse('external_context_refused');
        if(packaged&&(pre.typeReferenceDirectives.length||pre.importedFiles.some(ref=>!local(ref.fileName)&&ref.fileName!=='react'&&!Object.hasOwn(typingInput.policy.pins,ref.fileName))
            ||pre.referencedFiles.some(ref=>!local(ref.fileName))))refuse('typing_package_external_reference');
    }
    const manifest = preparedManifest || recordManifest(records);
    const directoryManifest = [...directories].map(([path, names]) => ({ path,
        files: [...names.files].sort(), directories: [...names.directories].sort() })).sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
    metadataBytes += bytes(binding) + bytes(policy);
    if (metadataBytes > limit.metadata_bytes || contentBytes + metadataBytes > limit.image_bytes) refuse('image_byte_budget');
    const imageIdentity = hashObject({ manifest, directoryManifest, policy_sha256: hashObject(policy),
        compiler_version: ts.version, compiler_source_sha256: binding.compiler_source_sha256 });
    const frozenBinding = canonical(binding), envelopeIdentity = hashObject({ imageIdentity, binding });
    const captureMs = performance.now() - started;
    checkTime();
    function replay(expectedBinding) {
        if (disposed) refuse('image_disposed');
        if (canonical(expectedBinding) !== frozenBinding) refuse('expected_binding_mismatch');
        const begin = performance.now(), calls = [];
        let callBytes = metadataBytes, maxObservedRss = process.memoryUsage().rss;
        function observe(operation, path = '') {
            if (performance.now() - begin > limit.replay_ms) refuse('replay_timeout');
            maxObservedRss = Math.max(maxObservedRss, process.memoryUsage().rss);
            if (maxObservedRss > limit.rss_observation_bytes) refuse('rss_observation_budget');
            const call = { operation, path_sha256: digest(path) };
            callBytes += bytes(call); calls.push(call);
            if (calls.length > limit.host_calls || callBytes > limit.metadata_bytes) refuse('host_observation_budget');
        }
        function query(operation, candidate) {
            if (typeof candidate !== 'string' || /[\\:\x00-\x1f]/.test(candidate)) refuse('query_path_refused');
            const path = posix.resolve(PROJECT, candidate);
            // The fixture owns the complete VIRTUAL '/' namespace, whose only
            // child is ROOT. Ancestor/library probes observe virtual absence,
            // never absence in the physical host filesystem.
            observe(operation, path); return path;
        }
        const readFile = name => {
            const path=query('readFile',name),entry=packageFiles.get(path);
            if(entry&&!['declaration','package_manifest'].includes(entry.kind))refuse('typing_package_unadmitted_read');
            return records.get(path)?.hostText;
        };
        const host = {
            fileExists: name => records.has(query('fileExists', name)), readFile,
            directoryExists: name => directories.has(query('directoryExists', name)),
            getDirectories: name => [...(directories.get(query('getDirectories', name))?.directories || [])].sort(),
            readDirectory: () => refuse('unsupported_read_directory'),
            realpath: name => { const path = query('realpath', name);
                if (!records.has(path) && !directories.has(path)) refuse('unknown_realpath'); return path; },
            getSourceFile: (name, languageVersionOrOptions) => {
                observe('getSourceFile', name);
                const text = readFile(name);
                return text === undefined ? undefined : ts.createSourceFile(name, text, languageVersionOrOptions, true);
            },
            getDefaultLibFileName: options => { observe('getDefaultLibFileName');
                const path = compilerRoot + '/' + ts.getDefaultLibFileName(options);
                if (!records.has(path)) refuse('default_library_missing'); return path; },
            getDefaultLibLocation: () => { observe('getDefaultLibLocation'); return compilerRoot; },
            writeFile: () => refuse('unexpected_write'),
            getCurrentDirectory: () => { observe('getCurrentDirectory'); return PROJECT; },
            getCanonicalFileName: name => { observe('getCanonicalFileName', name); return name; },
            useCaseSensitiveFileNames: () => { observe('useCaseSensitiveFileNames'); return true; },
            getNewLine: () => { observe('getNewLine'); return '\n'; },
        };
        const parsed = ts.parseJsonConfigFileContent(JSON.parse(canonical(config)), host, PROJECT, undefined, configPath);
        if (parsed.errors.length) refuse('config_parse_failure');
        const program = ts.createProgram({ rootNames: parsed.fileNames, options: parsed.options, host });
        const diagnostics = ts.getPreEmitDiagnostics(program);
        observe('replay_complete');
        const rows = diagnostics.map(diag => ({ code: diag.code, category: diag.category,
            file_sha256: diag.file ? digest(diag.file.fileName) : null,
            start: Number.isInteger(diag.start) ? diag.start : null }));
        if (bytes(rows) + callBytes > limit.metadata_bytes) refuse('result_metadata_budget');
        return { status: 'DIAGNOSTIC_EXPERIMENT_NOT_ADMITTED', image_sha256: imageIdentity,
            envelope_sha256: envelopeIdentity, diagnostics: rows, host_calls: calls,
            source_files: program.getSourceFiles().map(file => ({ path_sha256: digest(file.fileName), text_sha256: digest(file.text) })),
            metrics: { capture_ms: captureMs, replay_ms: performance.now() - begin,
                content_bytes: contentBytes, metadata_bytes: callBytes, max_observed_rss_bytes: maxObservedRss,
                rss_basis: 'samples_not_peak_or_hard_limit' },
            native_project_equivalent: false, semantic_lineage: false, production_enabled: false,
            raw_image_persisted: false, loaded_code_attested: false, descendant_quiescence: 'not_established' };
    }
    ingressCheck = null;
    const instance = Object.freeze({ replay, dispose() { disposed = true; records.clear(); directories.clear();if(preparedMode)folded.clear(); },
        summary: Object.freeze({ image_sha256: imageIdentity, envelope_sha256: envelopeIdentity,
            content_bytes: contentBytes, metadata_bytes: metadataBytes, file_count: records.size }) });
    completed = true;return instance;
    } finally {
        preparedInput = null;ingressCheck = null;preparedRows?.clear();
        if (preparedMode && !completed) {disposed = true;records.clear();directories.clear();folded.clear();}
    }
}

function createExperiment(ts, inputs, binding, policyInput, metadataInput = null, typingInput = null) {
    return buildExperiment(ts,inputs,binding,policyInput,metadataInput,typingInput);
}

// Closed flat metadata definitions only; nested producer refs are validated by
// the registered borrow owner. This is not a general JSON Schema interpreter.
function readIngressFlat(value,def) {
    if (!exact(def,['type','additionalProperties','required','properties']) || def.type !== 'object'
        || def.additionalProperties !== false || !Array.isArray(def.required) || !exact(def.properties,def.required)
        || !exact(value,def.required)) refuse('prepared_ingress_policy_refused');
    for (const key of def.required) {
        const rule=def.properties[key],item=value[key];
        if (!rule || Object.keys(rule).some(k=>!['type','const','pattern','minLength'].includes(k))
            || (rule.type==='object' ? !item || typeof item!=='object' || Array.isArray(item)
                : rule.type==='array' ? !Array.isArray(item) : !['string','boolean'].includes(rule.type) || typeof item!==rule.type)
            || Object.hasOwn(rule,'const') && hashObject(item)!==hashObject(rule.const)
            || rule.minLength!==undefined && (typeof item!=='string'||item.length<rule.minLength)
            || rule.pattern!==undefined && (rule.pattern!=='^[a-f0-9]{64}$'||!sha(item))) refuse('prepared_ingress_policy_refused');
    }
}

function createPreparedCompilerExperiment(ts,inputs,binding,policyInput,owner,admissionInput,options) {
    // Lazy canonical producer resolution avoids the pure-helper import cycle.
    const {_borrowPreparedCompilerTextSet:borrow,_disposePreparedCompilerTextSet:release}=require('./compiler_declaration_text.cjs');
    let candidate=null,held=null,text=null,settled=false;
    try {
        const policy=policyFor(policyInput),limit=policy.limits;
        if (policy.version!=='v1-synthetic-memory-only') refuse('prepared_ingress_profile_refused');
        const now=options?.now || (()=>performance.now()),deadline=options?.deadline;
        if (typeof now!=='function') refuse('prepared_ingress_deadline');
        const started=now();
        if (!Number.isFinite(started)||!Number.isFinite(deadline)||!(started<deadline&&deadline<=started+limit.capture_ms)) refuse('prepared_ingress_deadline');
        const checkTime=()=>{const time=now();if(!Number.isFinite(time)||time>=deadline)refuse('prepared_ingress_deadline');};
        text=JSON.stringify({binding,image_policy:policy,admission:admissionInput});
        const stageBytes=Buffer.byteLength(text,'utf8');
        if(stageBytes>limit.metadata_bytes)refuse('prepared_ingress_budget');
        held=JSON.parse(text);text=null;checkTime();
        if(!exact(held.admission,['policy','schema','expected','compiler_manifest']))refuse('prepared_ingress_context_refused');
        const {policy:ingress,schema,expected,compiler_manifest:manifest}=held.admission;
        readIngressFlat(ingress,schema?.$defs?.policy);readIngressFlat(expected,schema?.$defs?.expected);
        if(ingress.version!=='v1-private-prepared-compiler-image-ingress'||ingress.compiler_virtual_root!==COMPILER
            ||['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].some(k=>ingress[k]!==false)
            ||ingress.image_profile!=='private_input_image_experiment'||ingress.handoff_profile!=='private_compiler_declaration_text_handoff_preparation'
            ||ingress.model!=='synthetic_registered_base_image_v1'||ingress.result_status!=='PRIVATE_PREPARED_COMPILER_IMAGE_NOT_ADMITTED'
            ||ingress.schema_sha256!==hashObject(schema))refuse('prepared_ingress_policy_refused');
        if(hashObject(held.binding)!==expected.binding_sha256||hashObject(ingress)!==expected.ingress_policy_sha256
            ||hashObject(schema)!==expected.ingress_schema_sha256||hashObject(held.image_policy)!==expected.preparation.image_policy_sha256
            ||expected.preparation.preparation_policy_sha256!==ingress.handoff_policy_sha256
            ||expected.preparation.preparation_schema_sha256!==ingress.handoff_schema_sha256
            ||!manifest||hashObject(manifest)!==expected.preparation.manifest_sha256||manifest.mapping_root!==COMPILER
            ||manifest.session_id!==expected.stream_session_id||manifest.compiler_version!==held.binding.compiler_version
            ||manifest.compiler_source_sha256!==held.binding.compiler_source_sha256||!Array.isArray(manifest.files)||!manifest.files.length)refuse('prepared_ingress_context_refused');
        const ingressIdentity=hashObject({expected,manifest_sha256:expected.preparation.manifest_sha256,
            policy_sha256:expected.ingress_policy_sha256,schema_sha256:expected.ingress_schema_sha256});
        borrow(owner,expected.preparation,rows=>{
            checkTime();
            if(rows.length!==manifest.files.length||rows.some((row,i)=>!manifest.files[i]||row.name!==manifest.files[i].name
                ||row.size_bytes!==manifest.files[i].bytes||row.raw_sha256!==manifest.files[i].raw_sha256
                ||row.host_sha256!==manifest.files[i].host_text_sha256))refuse('prepared_ingress_context_refused');
            candidate=buildExperiment(ts,inputs,held.binding,held.image_policy,null,null,
                {rows,source_inputs_sha256:expected.source_inputs_sha256,checkTime});
        });
        checkTime();
        const knownMetadata=candidate.summary.metadata_bytes+stageBytes;
        if(knownMetadata>limit.metadata_bytes||candidate.summary.content_bytes+knownMetadata>limit.image_bytes)refuse('prepared_ingress_budget');
        const result=Object.freeze({replay:candidate.replay,dispose:candidate.dispose,
            summary:Object.freeze({...candidate.summary,status:ingress.result_status,prepared_ingress_sha256:ingressIdentity,
                ingress_metadata_bytes:stageBytes,admission_stage_known_metadata_bytes:knownMetadata,
                admission_stage_known_image_bytes:candidate.summary.content_bytes+knownMetadata,
                whole_image_within_limit:null,peak_rss_bytes:null,image_admitted:false,native_project_equivalent:false,
                production_enabled:false,semantic_lineage:false,cache_eligible:false,loaded_code_attested:false}),
            toJSON(){throw new TypeError('prepared_compiler_image_not_serializable');}});
        checkTime();settled=true;return result;
    } finally {
        if(!settled)candidate?.dispose();
        release(owner);held=text=null;
    }
}

module.exports = { createExperiment, ImageRefusal, hashObject, admitCompilerPackageMetadata, compilerMetadataPolicyFor, admitTypingPackages,
    _copyDecodeOwnedText, _finishOwnedText, createPreparedCompilerExperiment };

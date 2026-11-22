'use strict';
// Private finite calibration fixture. This is not a production collector or a CLI contract.
const fs = require('node:fs'), path = require('node:path'), os = require('node:os'), crypto = require('node:crypto');
const { createDeclarationExperiment } = require('../../core/owned_type_import_handoff.cjs');
const { singleTypeImport, observeReactProgram, observeDomProgram, observeDomLocations, observeBarrelAlias } = require('../../core/type_import_observation.cjs');
const { canonical } = require('../../core/owned_image_handoff.cjs');
const { admitCompilerPackageMetadata, compilerMetadataPolicyFor, createExperiment } = require('../../core/ts_input_image_experiment.cjs');
const { prepareCompilerInputObservation } = require('../../core/compiler_input_observation.cjs');
const { sourceRecords } = require('../../core/owned_declaration_handoff.cjs');
const {checkDiagnosticProjectionPolicy,createDiagnosticTap,attachDiagnosticProjection,diagnosticProjectionControl}=require('./owned_diagnostic_projection.cjs');
const {createDiagnosticCorrespondenceOwner,attachDiagnosticCorrespondence,disposeDiagnosticCorrespondenceOwner}=require('./owned_diagnostic_projection.cjs');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const fail = code => { const error = new Error(code); error.code = code; throw error; };
const digest = value => sha(canonical(value));
const flags = { native_project_equivalent: false, semantic_lineage: false, production_enabled: false };
const same = (a, b) => canonical(a) === canonical(b);

function compareArms(image, native, expectedEdges = 1) {
    for (const arm of [image, native]) {
        if (arm.status !== 'COMPLETE_FINITE_ARM' || arm.compiler_invocations !== 1 || arm.cached_resolution_reads !== expectedEdges
            || Object.entries(flags).some(([key, value]) => arm[key] !== value)) fail('calibration_arm_incomplete');
    }
    for (const key of ['inputs', 'roots', 'options', 'diagnostics', 'checked', 'edge',
        ...(image.source_kind || native.source_kind ? ['source_kind'] : []),
        ...(image.metadata_identity || native.metadata_identity ? ['metadata_identity'] : [])]) {
        if (!same(image[key], native[key])) fail('calibration_' + key + '_mismatch');
    }
    if(image.jsx_origin||native.jsx_origin)if(!same(image.jsx_origin,native.jsx_origin))fail('calibration_jsx_origin_mismatch');
    if(image.dom_observation||native.dom_observation)if(!same(image.dom_observation,native.dom_observation))fail('calibration_dom_observation_mismatch');
    if(image.imported_component||native.imported_component)if(!same(image.imported_component,native.imported_component))fail('calibration_imported_component_mismatch');
    return { status: 'FINITE_CONTEXTS_MATCHED_NOT_ADMITTED', ...flags,
        program_completeness_established: false, cache_eligible: false };
}

function captureCompilerMetadata(ts, admitted, policy) {
    policy = compilerMetadataPolicyFor(policy);
    const modulePath = require.resolve('typescript',{paths:[path.resolve(__dirname,'../../engines')]});
    const filename = path.join(path.dirname(path.dirname(modulePath)),'package.json');
    if (fs.realpathSync(filename) !== filename) fail('compiler_metadata_capture_alias');
    const fd = fs.openSync(filename,'r');
    let raw, readCalls = 0;
    try {
        const before = fs.fstatSync(fd);
        if (!before.isFile() || before.nlink !== 1 || before.size > policy.max_metadata_bytes) fail('compiler_metadata_capture_budget');
        const buffer = Buffer.alloc(before.size+1);
        let count = 0, n;
        while ((n=fs.readSync(fd,buffer,count,buffer.length-count,null))>0) count+=n;
        readCalls++;
        const after = fs.fstatSync(fd);
        if (count !== before.size || after.size !== before.size || after.mtimeMs !== before.mtimeMs)
            fail('compiler_metadata_capture_changed');
        raw = buffer.subarray(0,count);
    } finally {fs.closeSync(fd);}
    const expected = {raw_sha256:sha(raw),host_sha256:sha(raw.toString('utf8').replace(/^\ufeff/,'')),
        bytes:raw.length,policy_sha256:digest(policy),source_handoff_sha256:admitted.bindingSha256,
        compiler_version:admitted.binding.compiler_version,compiler_source_sha256:admitted.binding.compiler_source_sha256};
    const captured = admitCompilerPackageMetadata(ts,{policy,expected,raw},{
        ...expected,source_handoff_sha256:admitted.bindingSha256});
    return {captured, original_read_calls:readCalls};
}

function rowsFor(admitted, joined, libraries, metadata = null) {
    return [
        ...(admitted.sourceInputs?Object.entries(admitted.sourceInputs).map(([key,row])=>({role:joined.source_roster[key].role,
            virtual:joined.source_roster[key].compiler_path,name:joined.source_roster[key].atlas_path,raw:row.raw}))
            :[{ role: 'source', virtual: joined.compiler_virtual_path, name: joined.atlas_relative_path, raw: admitted.raw }]),
        { role: 'declaration', virtual: joined.auxiliary_virtual_path, name: joined.auxiliary_atlas_path, raw: admitted.auxiliaryRaw },
        { role: 'config', virtual: joined.config_virtual_path, name: joined.config_atlas_path, raw: admitted.configRaw },
        ...(admitted.jsxRaw ? [{role:'jsx_contract',virtual:joined.jsx_virtual_path,name:joined.jsx_atlas_path,raw:admitted.jsxRaw}] : []),
        ...(admitted.typingPackages?admitted.typingPackages.entries.map(row=>({role:'package:'+row.package+'/'+row.member,
            package:row.package,member:row.member,kind:row.kind,virtual:row.path,name:'node_modules/'+row.package+'/'+row.member,raw:row.raw})):[]),
        ...libraries.map(row => ({ role: 'compiler:' + path.posix.basename(row.path),
            virtual: metadata ? metadata.policy.library_virtual_root+'/'+path.posix.basename(row.path) : row.path,
            name: path.posix.basename(row.path), raw: row.raw })),
        ...(metadata ? [{role:'compiler_metadata',virtual:metadata.policy.metadata_virtual_path,name:'package.json',raw:metadata.raw}] : []),
    ].map(row => ({ ...row, raw_sha256: sha(row.raw),
        host_sha256: sha(row.raw.toString('utf8').replace(/^\ufeff/, '')) }));
}

function mappingFor(ts, records, scratch, libraryRoot) {
    const key = candidate => {
        if (typeof candidate !== 'string' || /[\x00-\x1f]/.test(candidate)
            || candidate.replace(/\\/g, '/').split('/').some(part => part === '.' || part === '..'))
            fail('calibration_path_refused');
        const normalized = candidate.replace(/\\/g, '/');
        return ts.sys.useCaseSensitiveFileNames ? normalized : normalized.toLowerCase();
    };
    const virtual = new Map(), native = new Map();
    const rows = records.map(row => ({ ...row,
        physical: path.join(row.role==='compiler_metadata' ? path.dirname(libraryRoot)
            : row.role.startsWith('compiler:') ? libraryRoot : scratch, row.name) }));
    for (const row of rows) {
        for (const [map, candidate] of [[virtual, row.virtual], [native, row.physical]]) {
            if (map.has(key(candidate))) fail('calibration_mapping_ambiguous');
            map.set(key(candidate), row);
        }
    }
    function role(candidate, lane) {
        const row = (lane === 'image' ? virtual : native).get(key(candidate));
        if (!row) fail('calibration_mapping_unavailable');
        return row;
    }
    const hashedVirtual = new Map(rows.map(row => [sha(row.virtual), row]));
    const packageDirectories=new Set();
    for(const row of rows.filter(row=>row.role.startsWith('package:'))){
        let directory=path.dirname(row.physical);
        while(key(directory).startsWith(key(scratch).replace(/\\/g,'/')+'/')){
            packageDirectories.add(key(directory));directory=path.dirname(directory);
        }
    }
    const inputRows = rows.filter(row => !row.role.startsWith('compiler:')).map(row => ({
        role: row.role, raw_sha256: row.raw_sha256, host_sha256: row.host_sha256 })).sort((a,b) => a.role.localeCompare(b.role));
    return { rows, key, role, inputRows, hashedVirtual, native, packageDirectories,
        summary: rows.map(row => ({ role: row.role, virtual_path_sha256: sha(row.virtual),
            native_path_sha256: sha(row.physical) })),
        path_case_basis: ts.sys.useCaseSensitiveFileNames ? 'case_sensitive' : 'typescript_platform_case_insensitive' };
}

function optionsFor(options, mapping, lane) {
    const result = { ...options };
    if (typeof result.configFilePath !== 'string' || mapping.role(result.configFilePath, lane).role !== 'config')
        fail('calibration_options_config_unavailable');
    result.configFilePath = 'role:config';
    return JSON.parse(canonical(result));
}

function checkedFor(files, mapping, lane) {
    const roles = new Set();
    return files.map(file => {
        const row = lane === 'image' ? mapping.hashedVirtual.get(file.path_sha256) : mapping.role(file.fileName, lane);
        const text = lane === 'image' ? file.text_sha256 : sha(file.text);
        if (!row || roles.has(row.role) || text !== row.host_sha256) fail('calibration_program_text_refused');
        roles.add(row.role);
        return { role: row.role, text_sha256: text };
    }).sort((a,b) => a.role.localeCompare(b.role));
}

function diagnosticRows(diagnostics, mapping, lane) {
    return diagnostics.map(diag => {
        let role = null;
        if (lane === 'image' && diag.file_sha256 !== null) {
            role = mapping.hashedVirtual.get(diag.file_sha256)?.role;
            if (!role) fail('calibration_diagnostic_path_refused');
        } else if (lane === 'native' && diag.file) role = mapping.role(diag.file.fileName, lane).role;
        const start = Number.isInteger(diag.start) ? diag.start : null;
        if (!Number.isInteger(diag.code) || !Number.isInteger(diag.category)) fail('calibration_diagnostic_refused');
        return { role, code: diag.code, category: diag.category, start };
    }).sort((a,b) => canonical(a).localeCompare(canonical(b)));
}

function edgeFor(ts, program, mapping, binding) {
    const sourceRow = mapping.rows.find(row => row.role === 'source'), targetRow = mapping.rows.find(row => row.role === 'declaration');
    if (typeof program.getResolvedModuleFromModuleSpecifier !== 'function') fail('calibration_cached_api_unavailable');
    const source = program.getSourceFile(sourceRow.physical);
    if (!source || sha(source.text) !== sourceRow.host_sha256) fail('calibration_native_source_refused');
    const imported = singleTypeImport(ts, source, './types');
    const resolution = program.getResolvedModuleFromModuleSpecifier(imported.node.moduleSpecifier, source)?.resolvedModule;
    if (!resolution || mapping.role(resolution.resolvedFileName, 'native').role !== 'declaration'
        || resolution.extension !== ts.Extension.Dts || resolution.isExternalLibraryImport !== false || resolution.packageId)
        fail('calibration_cached_edge_refused');
    const target = program.getSourceFile(resolution.resolvedFileName);
    if (!target || !target.isDeclarationFile || sha(target.text) !== targetRow.host_sha256
        || sha(imported.name) !== binding.import_name_sha256) fail('calibration_native_target_refused');
    return { source_role: 'source', target_role: 'declaration', source_host_sha256: sourceRow.host_sha256,
        target_host_sha256: targetRow.host_sha256, import_name_sha256: sha(imported.name),
        module_specifier_sha256: sha('./types'), type_only: true, extension: '.d.ts', external: false };
}

// Operates on our own mkdtemp root only. Known library files are read-only; no general host fallback.
function withFilesystem(ts, records, limits, operation, mutate, copiedPackage = false) {
    const temporaryParent = fs.realpathSync(os.tmpdir());
    const scratch = fs.mkdtempSync(path.join(temporaryParent, 'sage-context-calibration-'));
    const project = copiedPackage ? path.join(scratch,'project') : scratch;
    const libraryRoot = copiedPackage ? path.join(scratch,'compiler','node_modules','typescript','lib')
        : path.dirname(require.resolve('typescript', { paths: [path.resolve(__dirname, '../../engines')] }));
    let result, thrown, cleanup = false, observedContext;
    try {
        if (copiedPackage) {fs.mkdirSync(project);fs.mkdirSync(libraryRoot,{recursive:true});}
        const mapping = mappingFor(ts, records, project, libraryRoot);
        for (const row of mapping.rows.filter(row => copiedPackage || !row.role.startsWith('compiler:'))){
            if(!path.resolve(row.physical).startsWith(path.resolve(scratch)+path.sep))fail('calibration_copy_target_refused');
            fs.mkdirSync(path.dirname(row.physical),{recursive:true});
            fs.writeFileSync(row.physical, row.raw, { flag: 'wx' });
        }
        const observations = [], reads = {}, readIdentities = {}, begin = performance.now();
        let deniedAccess = null;
        let metadataBytes = 0;
        function observe(operation, candidate, status) {
            const row = { operation, path_sha256: sha(candidate), status };
            metadataBytes += Buffer.byteLength(canonical(row));
            observations.push(row);
            if (observations.length > limits.host_calls || metadataBytes > limits.metadata_bytes
                || performance.now() - begin > limits.replay_ms) fail('calibration_host_budget');
        }
        function inspect(candidate, operation) {
            if (!path.isAbsolute(candidate)) fail('calibration_path_refused');
            const key = mapping.key(candidate);
            // lstat probes physical absence; no synthetic absence for unknown ancestors/packages.
            let stat;
            try { stat = fs.lstatSync(candidate); }
            catch (error) {
                if (!['ENOENT', 'ENOTDIR'].includes(error.code)) fail('calibration_host_unavailable');
                observe(operation, candidate, 'physically_missing'); return { key, stat: null };
            }
            if (stat.isSymbolicLink()) fail('calibration_alias_refused');
            if (fs.realpathSync(candidate).replace(/\\/g, '/') !== candidate.replace(/\\/g, '/'))
                fail('calibration_alias_refused');
            observe(operation, candidate, stat.isFile() ? 'physical_file' : 'physical_directory');
            return { key, stat };
        }
        function foreignContent(candidate, operation) {
            deniedAccess = {operation, path_sha256:sha(candidate), content_read:false,
                location: mapping.key(candidate) === mapping.key(path.join(path.dirname(libraryRoot),'package.json'))
                    ? 'compiler_package_metadata' : 'outside_admitted_file_roster'};
            fail('calibration_foreign_content_refused');
        }
        function readFile(candidate) {
            const {key, stat} = inspect(candidate, 'readFile');
            const row = mapping.native.get(key);
            if (!row && !stat) return undefined;
            if (!row) foreignContent(candidate, 'readFile');
            if(row.role.startsWith('package:')&&!['declaration','package_manifest'].includes(row.kind))fail('calibration_unadmitted_package_read');
            if (!stat || !stat.isFile() || stat.nlink !== 1 || stat.size !== row.raw.length) fail('calibration_copy_changed');
            const fd = fs.openSync(candidate, 'r');
            let raw;
            try {
                const before = fs.fstatSync(fd), cap = row.role.startsWith('compiler:') ? limits.compiler_file_bytes : limits.source_file_bytes;
                if (before.size !== row.raw.length || before.size > cap || before.dev !== stat.dev || before.ino !== stat.ino)
                    fail('calibration_copy_changed');
                const buffer = Buffer.alloc(before.size + 1);
                let size = 0, n;
                while ((n = fs.readSync(fd, buffer, size, buffer.length - size, null)) > 0) {
                    size += n; if (size > before.size) fail('calibration_copy_changed');
                }
                const after = fs.fstatSync(fd);
                if (size !== before.size || after.size !== before.size || after.mtimeMs !== before.mtimeMs) fail('calibration_copy_changed');
                raw = buffer.subarray(0,size);
            } finally { fs.closeSync(fd); }
            if (sha(raw) !== row.raw_sha256) fail('calibration_copy_changed');
            const text = new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(raw).replace(/^\ufeff/, '');
            if (sha(text) !== row.host_sha256) fail('calibration_host_text_refused');
            reads[row.role] = (reads[row.role] || 0) + 1;
            readIdentities[row.role] = {raw_sha256:sha(raw),host_sha256:sha(text)};
            return text;
        }
        function fileExists(candidate) {
            const {key,stat} = inspect(candidate, 'fileExists');
            if (stat?.isFile() && !mapping.native.has(key)) foreignContent(candidate, 'fileExists');
            return !!stat?.isFile();
        }
        function directoryExists(candidate) { return !!inspect(candidate, 'directoryExists').stat?.isDirectory(); }
        function realpath(candidate) {
            const {key,stat} = inspect(candidate, 'realpath');
            if (!stat || !mapping.native.has(key)&&!(stat.isDirectory()&&mapping.packageDirectories.has(key))) fail('calibration_realpath_refused');
            return candidate;
        }
        const context = { mapping, readFile, fileExists, directoryExists, realpath,
            readDirectory: () => fail('calibration_directory_enumeration_refused'),
            getDirectories: () => fail('calibration_directory_enumeration_refused'),
            summary: () => ({host_calls: observations.length, observations_sha256: digest(observations), reads:{...reads},
                read_identities:{...readIdentities},
                denied_access:deniedAccess}),
            scratch:project, libraryRoot };
        observedContext = context;
        if (mutate) mutate(context);
        result = operation(context);
    } catch (error) { thrown = error; if(observedContext) error.native_observation=observedContext.summary(); }
    finally {
        // Verify the exact resolved target before recursive removal; never accept a caller-supplied directory.
        if (path.dirname(scratch) !== temporaryParent || !path.basename(scratch).startsWith('sage-context-calibration-')
            || !path.isAbsolute(scratch) || fs.lstatSync(scratch).isSymbolicLink()
            || fs.realpathSync(scratch) !== scratch) fail('calibration_cleanup_target_refused');
        fs.rmSync(scratch, { recursive: true, force: false });
        cleanup = !fs.existsSync(scratch);
    }
    if (thrown) { thrown.calibration_cleanup = cleanup; throw thrown; }
    return {...result, temporary_copy_removed: cleanup};
}

function runCalibration(ts, admitted, policies, libraries, control, metadataPolicy = null) {
    const react=!!admitted.typingPackages;
    const correspondenceMode=control==='calibration-metadata-correspondence';
    const projectionMode=correspondenceMode||control==='calibration-metadata-projection'||control.startsWith('calibration-metadata-projection-unit-');
    const metadataMode = control.startsWith('calibration-metadata');
    if (metadataMode && control !== 'calibration-metadata' && !projectionMode && !control.startsWith('calibration-metadata-unit-'))
        fail('compiler_metadata_unknown_control');
    if (metadataMode && !metadataPolicy) fail('compiler_metadata_policy_missing');
    const capture = metadataMode ? captureCompilerMetadata(ts,admitted,metadataPolicy) : null;
    const metadata = capture?.captured || null;
    if(projectionMode)checkDiagnosticProjectionPolicy(admitted,policies);
    if(control.startsWith('calibration-metadata-projection-unit-')){
        const records=rowsFor(admitted,policies.joined,libraries,metadata),scratch=path.join(os.tmpdir(),'sage-diagnostic-model');
        const mapping=mappingFor(ts,records,scratch,path.join(scratch,'compiler','node_modules','typescript','lib'));
        return diagnosticProjectionControl(ts,admitted,policies,mapping,control.slice('calibration-metadata-projection-unit-'.length));
    }
    if (control.startsWith('calibration-metadata-unit-'))
        return metadataControl(ts,admitted,metadata,control.slice('calibration-metadata-unit-'.length),policies,libraries);
    const records = rowsFor(admitted, policies.joined, libraries, metadata), limits = policies.image.limits;
    if (control.startsWith('calibration-unit-')) return unitControl(ts, records, limits, control.slice('calibration-unit-'.length));
    let instance, correspondenceOwner, disposed = false, imageArm = null, nativeArm = null, nativeProgramReturned = false;
    let imagePrograms = 0, imageCache = 0, nativePrograms = 0, nativeCache = 0, memoryFallbackAttempts = 0;
    try {
        return withFilesystem(ts, records, limits, context => {
            const {mapping} = context;
            let imageOptions, imageRoots;
            const imageApi = Object.create(ts);
            Object.defineProperty(imageApi,'createProgram',{value: options => {
                if (++imagePrograms !== 1) fail('calibration_extra_program');
                const program = ts.createProgram(options);
                imageOptions = optionsFor(program.getCompilerOptions(),mapping,'image');
                imageRoots = program.getRootFileNames().map(name => mapping.role(name,'image').role).sort();
                const observedProgram = Object.create(program);
                Object.defineProperty(observedProgram, 'getResolvedModuleFromModuleSpecifier', {value: (...args) => {
                    imageCache++;
                    return program.getResolvedModuleFromModuleSpecifier(...args);
                }});
                return observedProgram;
            }});
            const imageTap=projectionMode?createDiagnosticTap(imageApi,admitted,policies,mapping,'image'):null;
            const nativeTap=projectionMode?createDiagnosticTap(ts,admitted,policies,mapping,'native'):null;
            instance = createDeclarationExperiment(imageTap?imageTap.api:imageApi, admitted, policies, libraries, metadata);
            if(correspondenceMode){correspondenceOwner=createDiagnosticCorrespondenceOwner(admitted,policies,instance);
                imageTap.bindOwner(correspondenceOwner);nativeTap.bindOwner(correspondenceOwner);}
            let rawImage;
            const restore = [];
            try {
                for (const [owner, keys] of [[fs, ['readFileSync','openSync','statSync','lstatSync','readdirSync','existsSync','realpathSync']],
                    [ts.sys, ['readFile','fileExists','directoryExists','getDirectories','readDirectory','realpath']]]) {
                    for (const key of keys) if (typeof owner[key] === 'function') {
                        const original = owner[key];
                        owner[key] = () => {memoryFallbackAttempts++; fail('calibration_image_live_fallback');};
                        restore.push(() => {owner[key] = original;});
                    }
                }
                rawImage = instance.replay(admitted.binding);
            } finally { for (const undo of restore.reverse()) undo(); }
            const observed = rawImage.type_import_observation;
            const image = {status:'COMPLETE_FINITE_ARM', ...flags, compiler_invocations:imagePrograms, cached_resolution_reads:imageCache,
                inputs:mapping.inputRows, roots:imageRoots, options:imageOptions,
                diagnostics:diagnosticRows(rawImage.diagnostics,mapping,'image'), checked:checkedFor(rawImage.source_files,mapping,'image'),
                edge:{source_role:'source',target_role:'declaration',source_host_sha256:observed.source_host_sha256,
                    target_host_sha256:observed.target_host_sha256, import_name_sha256:observed.import_name_sha256,
                    module_specifier_sha256:observed.module_specifier_sha256,type_only:observed.type_only,extension:observed.extension,
                    external:observed.is_external_library_import},
                host_observation:{host_calls:rawImage.host_calls.length,observations_sha256:digest(rawImage.host_calls)}};
            imageArm = image;
            if(react){
                const projected=reactProjection(observed);
                image.edge=projected.edges;image.jsx_origin=projected.jsx_origin;
                if(projected.imported_component)image.imported_component=projected.imported_component;
                if(rawImage.compiler_input_observation.project_sources){image.project_sources=rawImage.compiler_input_observation.project_sources;image.input_observation=rawImage.compiler_input_observation;}
                if(projected.dom_observation){
                    image.dom_observation=projected.dom_observation;
                    image.dom_program_reads=rawImage.host_calls.filter(row=>row.operation==='readFile'
                        &&row.path_sha256===sha(policies.image.dom_library.virtual_path)).length;
                    if(image.dom_program_reads<1)fail('calibration_dom_read_not_observed');
                }
                image.package_observation=observed;
                image.typing_packages=rawImage.compiler_input_observation.typing_packages;
            }
            if(rawImage.source_script_kind){
                image.source_kind=rawImage.source_script_kind;
                image.synthetic_jsx_contract=rawImage.compiler_input_observation.synthetic_jsx_contract;
            }
            if (metadata) {
                image.compiler_metadata = rawImage.compiler_package_metadata;
                image.metadata_identity = {raw_sha256:image.compiler_metadata.raw_sha256,
                    host_sha256:image.compiler_metadata.host_sha256,policy_sha256:image.compiler_metadata.policy_sha256,
                    source_handoff_sha256:image.compiler_metadata.source_handoff_sha256};
            }
            const configRow = mapping.rows.find(row => row.role === 'config');
            // Validate every copied project buffer, including config, before creating the filesystem Program.
            for (const row of mapping.rows.filter(row => !row.role.startsWith('compiler:')&&!row.role.startsWith('package:'))) context.readFile(row.physical);
            const configText = context.readFile(configRow.physical);
            const config = ts.parseConfigFileTextToJson(configRow.physical,configText);
            if (config.error) fail('calibration_config_refused');
            const parsed = ts.parseJsonConfigFileContent(config.config, context, context.scratch, undefined, configRow.physical);
            if (parsed.errors.length) fail('calibration_config_refused');
            const host = ts.createCompilerHost(parsed.options,true);
            // Mutate in place: the standard getSourceFile closure calls this host.readFile.
            for (const key of ['readFile','fileExists','directoryExists','getDirectories','readDirectory','realpath'])
                host[key] = context[key];
            host.getCurrentDirectory = () => context.scratch;
            if (metadata) {
                host.getDefaultLibLocation = () => context.libraryRoot;
                host.getDefaultLibFileName = options => path.join(context.libraryRoot,ts.getDefaultLibFileName(options));
            }
            host.writeFile = () => fail('calibration_write_refused');
            if (++nativePrograms !== 1) fail('calibration_extra_program');
            const readsBefore = metadata ? context.summary().reads.compiler_metadata || 0 : 0;
            const packageReadsBefore={...context.summary().reads};
            const program = ts.createProgram({rootNames:parsed.fileNames,options:parsed.options,host});
            nativeProgramReturned = true;
            const diagnostics = (nativeTap?nativeTap.api:ts).getPreEmitDiagnostics(program);
            let edge,jsxOrigin,domObservation,componentFact;
            if(react){
                const observedProgram=Object.create(program);
                Object.defineProperty(observedProgram,'getResolvedModuleFromModuleSpecifier',{value:(...args)=>{nativeCache++;return program.getResolvedModuleFromModuleSpecifier(...args);}});
                const sourceRow=mapping.rows.find(row=>row.role===(admitted.sourceInputs?'source:component':'source'));
                const entryRow=mapping.rows.find(row=>row.role==='source:entry');
                const barrelRow=mapping.rows.find(row=>row.role==='source:barrel');
                const locationSource=admitted.sourceInputs?admitted.sourceInputs.component:admitted;
                const projected=reactProjection(observeReactProgram(ts,observedProgram,{
                    sourcePath:sourceRow.physical,dtoPath:mapping.rows.find(row=>row.role==='declaration').physical,
                    sourceHostSha256:sourceRow.host_sha256,dtoHostSha256:admitted.binding.auxiliary_host_sha256,
                    entrySource:entryRow?{path:entryRow.physical,role:entryRow.role,host_sha256:entryRow.host_sha256}:null,
                    componentSource:entryRow?{path:sourceRow.physical,role:sourceRow.role,host_sha256:sourceRow.host_sha256}:null,
                    barrelSource:barrelRow?{path:barrelRow.physical,role:barrelRow.role,host_sha256:barrelRow.host_sha256}:null,
                    pins:admitted.typingPackages.policy.pins,componentContract:policies.joined.component_contract,
                    locationPolicy:policies.joined.source_location_contract||null,
                    locationBinding:policies.joined.source_location_contract?{parent_handoff_sha256:admitted.bindingSha256,
                        source_raw_sha256:sha(locationSource.raw),source_host_sha256:sha(locationSource.text.replace(/^\ufeff/,'')),
                        leading_bom_utf16_units_removed:locationSource.text.startsWith('\ufeff')?1:0}:null,
                    domLibrary:policies.image.dom_library?{...policies.image.dom_library,
                        path:mapping.rows.find(row=>row.role==='compiler:lib.dom.d.ts').physical}:null,
                    packageRows:mapping.rows.filter(row=>row.role.startsWith('package:')).map(row=>({...row,path:row.physical}))}));
                edge=projected.edges;jsxOrigin=projected.jsx_origin;domObservation=projected.dom_observation;componentFact=projected.imported_component;
            }else{nativeCache++;edge=edgeFor(ts,program,mapping,admitted.binding);}
            const native = {status:'COMPLETE_FINITE_ARM', ...flags,compiler_invocations:nativePrograms,cached_resolution_reads:nativeCache,
                inputs:mapping.inputRows,roots:program.getRootFileNames().map(name=>mapping.role(name,'native').role).sort(),
                options:optionsFor(program.getCompilerOptions(),mapping,'native'),
                diagnostics:diagnosticRows(diagnostics,mapping,'native'),checked:checkedFor(program.getSourceFiles(),mapping,'native'),
                edge,host_observation:context.summary()};
            nativeArm = native;
            if(react){
                native.jsx_origin=jsxOrigin;
                if(componentFact)native.imported_component=componentFact;
                if(admitted.sourceInputs){
                    native.source_program_reads={};
                    for(const row of mapping.rows.filter(row=>row.role.startsWith('source:'))){
                        const count=(context.summary().reads[row.role]||0)-(packageReadsBefore[row.role]||0);
                        if(count<1||!program.getSourceFile(row.physical))fail('calibration_source_read_not_observed');
                        native.source_program_reads[row.role]=count;
                    }
                }
                if(domObservation){
                    native.dom_observation=domObservation;
                    native.dom_program_reads=(context.summary().reads['compiler:lib.dom.d.ts']||0)-(packageReadsBefore['compiler:lib.dom.d.ts']||0);
                    if(native.dom_program_reads<1)fail('calibration_dom_read_not_observed');
                }
                native.package_program_reads={};
                for(const row of mapping.rows.filter(row=>row.role.startsWith('package:'))){
                    const count=(context.summary().reads[row.role]||0)-(packageReadsBefore[row.role]||0);
                    if(count>0)native.package_program_reads[row.role]=count;
                    if(program.getSourceFile(row.physical)&&count<1)fail('calibration_package_read_not_observed');
                }
            }
            if(rawImage.source_script_kind){
                const sourceRow=mapping.rows.find(row=>row.role==='source');
                native.source_kind=admitted.sourceInputs?Object.fromEntries(mapping.rows.filter(row=>row.role.startsWith('source:')).map(row=>[row.role,ts.ScriptKind[program.getSourceFile(row.physical)?.scriptKind]]))
                    :ts.ScriptKind[program.getSourceFile(sourceRow.physical)?.scriptKind];
                const jsx=mapping.rows.find(row=>row.role==='jsx_contract');
                if(!react&&(!jsx||!native.checked.some(row=>row.role==='jsx_contract')||!native.host_observation.reads.jsx_contract))
                    fail('calibration_jsx_input_unobserved');
            }
            if (metadata) {
                const read = context.summary().read_identities.compiler_metadata;
                native.compiler_metadata = {version:metadata.policy.version,authority:metadata.policy.authority,
                    role:metadata.policy.metadata_role,...read,raw_bytes:metadata.expected.bytes,
                    policy_sha256:metadata.expected.policy_sha256,source_handoff_sha256:metadata.expected.source_handoff_sha256,
                    read_calls:(context.summary().reads.compiler_metadata || 0)-readsBefore,reported_program_source:false};
                native.metadata_identity = {raw_sha256:read?.raw_sha256,host_sha256:read?.host_sha256,
                    policy_sha256:metadata.expected.policy_sha256,source_handoff_sha256:metadata.expected.source_handoff_sha256};
                if (image.compiler_metadata.read_calls < 1 || native.compiler_metadata.read_calls < 1)
                    fail('calibration_metadata_read_not_observed');
            }
            const comparison = compareArms(image,native,policies.joined.max_import_edges);
            const result={...comparison,image,native,role_mapping_sha256:digest(mapping.summary),role_mapping:mapping.summary,
                path_case_basis:mapping.path_case_basis,diagnostic_coordinate_basis:'utf16_host_text_after_one_bom_removal',
                observed_image_fallback_attempts:memoryFallbackAttempts,
                ...(metadata ? {compiler_metadata_capture:{original_read_calls:capture.original_read_calls,
                    raw_sha256:metadata.expected.raw_sha256,host_sha256:metadata.expected.host_sha256,
                    bytes:metadata.expected.bytes,policy_sha256:metadata.expected.policy_sha256,
                    source_handoff_sha256:metadata.expected.source_handoff_sha256,
                    compiler_version:metadata.expected.compiler_version,
                    compiler_source_sha256:metadata.expected.compiler_source_sha256},
                    copied_compiler_libraries:libraries.length} : {}),
                image_disposed_refused:disposed,raw_image_persisted:false,authority:'finite_private_calibration_only'};
            if(projectionMode)attachDiagnosticProjection(result,imageTap,nativeTap,rawImage,policies);
            if(correspondenceMode)attachDiagnosticCorrespondence(result,correspondenceOwner,policies);
            disposeDiagnosticCorrespondenceOwner(correspondenceOwner);instance.dispose();
            try {instance.replay(admitted.binding);} catch(error) {disposed = error.code === 'declaration_disposed';}
            result.image_disposed_refused=disposed;return result;
        },undefined,metadataMode);
    } catch(error) {
        return {status:nativeArm ? 'FINITE_CONTEXTS_MISMATCHED_NOT_ADMITTED' : 'CONTEXT_COMPARISON_INCOMPLETE_NOT_ADMITTED',
            code:error.code || 'calibration_unexpected_error',image:imageArm,
            native:nativeArm || {status:'INCOMPLETE_FINITE_ARM',...flags,compiler_invocations:nativePrograms,
                program_returned:nativeProgramReturned,cached_resolution_reads:nativeCache,
                host_observation:error.native_observation || null},
            temporary_copy_removed:error.calibration_cleanup === true,
            observed_image_fallback_attempts:memoryFallbackAttempts,raw_image_persisted:false,
            ...flags,program_completeness_established:false,cache_eligible:false,
            authority:'finite_private_calibration_only'};
    } finally {disposeDiagnosticCorrespondenceOwner(correspondenceOwner);if(instance) instance.dispose();}
}


function reactProjection(value){
    const edges=value.edges.map(({source_path_sha256,target_path_sha256,...row})=>row);
    const {source_path_sha256,...jsx_origin}=value.jsx_origin;
    return {edges,jsx_origin,...(value.imported_component?{imported_component:value.imported_component}:{}),...(value.dom_observation?{dom_observation:value.dom_observation}:{})};
}

function unitControl(ts,records,limits,control) {
    let result;
    try {
        if (['source_copy','declaration_copy','config_copy','foreign_read','foreign_probe','missing_copy','throw_cleanup','directory_query','parent_path','alias'].includes(control)) {
            result = withFilesystem(ts,records,limits,ctx => {
                const role = control.endsWith('_copy') ? control.slice(0,-5) : 'source';
                const row = ctx.mapping.rows.find(row=>row.role===role) || ctx.mapping.rows.find(row=>row.role==='config');
                if (control === 'throw_cleanup') fail('calibration_injected_failure');
                if (control === 'foreign_read') ctx.readFile(path.join(ctx.libraryRoot,'typescript.js'));
                else if (control === 'foreign_probe') ctx.fileExists(path.join(ctx.scratch,'foreign.d.ts'));
                else if (control === 'directory_query') ctx.readDirectory(ctx.scratch);
                else if (control === 'parent_path') ctx.readFile(ctx.scratch + '/sub/../entry.ts');
                else ctx.readFile(row.physical);
                fail('calibration_unit_did_not_refuse');
            },ctx => {
                if (control.endsWith('_copy') && control !== 'missing_copy') {
                    const row = ctx.mapping.rows.find(row=>row.role===control.slice(0,-5)); fs.writeFileSync(row.physical,'changed');
                }
                if(control==='missing_copy') fs.unlinkSync(path.join(ctx.scratch,'tsconfig.json'));
                if(control==='foreign_probe') fs.writeFileSync(path.join(ctx.scratch,'foreign.d.ts'),'not admitted');
                if(control==='alias') {
                    const row=ctx.mapping.rows.find(row=>row.role==='source');
                    fs.unlinkSync(row.physical); fs.linkSync(ctx.mapping.rows.find(row=>row.role==='declaration').physical,row.physical);
                }
            });
        } else {
            const arm={status:'COMPLETE_FINITE_ARM',...flags,compiler_invocations:1,cached_resolution_reads:1,
                inputs:[{role:'source',raw_sha256:'a'}],roots:['source','declaration'],options:{strict:true},
                diagnostics:[{code:2322,category:1,role:'source',start:5}],checked:[{role:'source',text_sha256:'b'}],
                edge:{source_role:'source',target_role:'declaration'}};
            const other=JSON.parse(JSON.stringify(arm));
            if(control==='matching') return {status:'MODELED_MATCH',comparison:compareArms(arm,other),
                real_compiler_invocations:0,raw_image_persisted:false,...flags};
            if(control==='options')other.options.strict=false;
            else if(control==='diagnostics')other.diagnostics[0].start++;
            else if(control==='duplicates')other.diagnostics.push({...other.diagnostics[0]});
            else if(control==='checked')other.checked[0].text_sha256='c';
            else if(control==='edge')other.edge.target_role='source';
            else if(control==='inputs')other.inputs[0].raw_sha256='c';
            else if(control==='roots')other.roots=['source'];
            else if(control==='mapping') {
                mappingFor(ts,[records[0],{...records[0],role:'foreign'}],os.tmpdir(),os.tmpdir());
                fail('calibration_unit_did_not_refuse');
            }
            else if(control==='extra_program')other.compiler_invocations=2;
            else if(control==='missing_cache')other.cached_resolution_reads=0;
            else if(control==='incomplete')other.status='UNAVAILABLE';
            else if(control==='promotion')other.semantic_lineage=true;
            else fail('calibration_unknown_control');
            compareArms(arm,other); fail('calibration_unit_did_not_refuse');
        }
    }catch(error) {
        result={status:'MODELED_REFUSAL',code:error.code || 'calibration_unit_error',
            temporary_copy_removed:error.calibration_cleanup ?? null};
    }
    return {...result,real_compiler_invocations:0,raw_image_persisted:false,...flags};
}
function metadataControl(ts,admitted,metadata,control,policies,libraries) {
    const owner={source_handoff_sha256:admitted.bindingSha256,compiler_version:admitted.binding.compiler_version,
        compiler_source_sha256:admitted.binding.compiler_source_sha256};
    const value={policy:JSON.parse(canonical(metadata.policy)),expected:JSON.parse(canonical(metadata.expected)),raw:Buffer.from(metadata.raw)};
    const updateRaw=raw=>{
        value.raw=raw; value.expected.raw_sha256=sha(raw); value.expected.host_sha256=sha(raw.toString('utf8').replace(/^\ufeff/,''));
        value.expected.bytes=raw.length;
    };
    try {
        if(control==='barrel_bridge_changed'){
            policies.bridges.private_owned_text_bridge.max_source_bytes--;
            createDeclarationExperiment(ts,admitted,policies,libraries,metadata);
            fail('barrel_bridge_change_not_refused');
        }
        if(control.startsWith('barrel_alias_')){
            const records=sourceRecords(admitted,policies.joined),parse=key=>ts.createSourceFile(records[key].path,
                admitted.sourceInputs[key].text.replace(/^\ufeff/,''),ts.ScriptTarget.Latest,true);
            const entry=parse('entry'),barrel=parse('barrel'),component=parse('component'),fn=component.statements[2];
            const imported=entry.statements[1].importClause.namedBindings.elements[0],exported=barrel.statements[0].exportClause.elements[0];
            const module={declarations:[barrel]},target={declarations:[fn]},foreign={declarations:[{}]};
            const entryAlias={flags:ts.SymbolFlags.Alias,declarations:[imported]},barrelAlias={flags:ts.SymbolFlags.Alias,name:'Field',declarations:[exported]};
            let exports=[barrelAlias],first=barrelAlias,second=target,flattened=target;
            const checker={getExportsOfModule:()=>exports,getSymbolAtLocation:node=>node===barrel?module
                :node===imported.name?entryAlias:node===exported.name?barrelAlias:node===fn.name?target:null,
                getImmediateAliasedSymbol:symbol=>symbol===entryAlias?first:second,getAliasedSymbol:()=>flattened};
            if(control==='barrel_alias_api')checker.getImmediateAliasedSymbol=undefined;
            if(control==='barrel_alias_flattened')first=target;
            if(control==='barrel_alias_first')first=foreign;
            if(control==='barrel_alias_second')second=foreign;
            if(control==='barrel_alias_final')flattened=foreign;
            if(control==='barrel_alias_extra')exports=[barrelAlias,foreign];
            if(control==='barrel_alias_module')module.declarations=[{}];
            if(control==='barrel_alias_entry_owner')entryAlias.declarations=[{}];
            if(control==='barrel_alias_export_owner')barrelAlias.declarations=[{}];
            if(control==='barrel_alias_function_owner')target.declarations=[{}];
            if(control==='barrel_alias_flags')barrelAlias.flags=ts.SymbolFlags.Function;
            const observed=observeBarrelAlias(ts,checker,entry,barrel,fn,records.barrel);
            if(control!=='barrel_alias_valid')fail('barrel_alias_control_not_refused');
            return {status:'METADATA_MODELED_OBSERVATION',observation:observed,real_compiler_invocations:0,raw_image_persisted:false};
        }
        if(control.startsWith('barrel_edge_')){
            const joined=policies.joined,records=sourceRecords(admitted,joined),files=new Map();
            for(const [key,row] of Object.entries(records))files.set(row.path,ts.createSourceFile(row.path,admitted.sourceInputs[key].text.replace(/^\ufeff/,''),ts.ScriptTarget.Latest,true));
            files.set(joined.auxiliary_virtual_path,ts.createSourceFile(joined.auxiliary_virtual_path,admitted.auxiliaryRaw.toString('utf8'),ts.ScriptTarget.Latest,true));
            const react=admitted.typingPackages.entries.find(row=>row.package==='@types/react'&&row.member==='index.d.ts');
            files.set(react.path,{fileName:react.path,isDeclarationFile:true,text:react.raw.toString('utf8')});
            let reads=0;
            const program={getSourceFile:path=>path===records.barrel.path&&++reads>1&&control==='barrel_edge_text'?{...files.get(path),text:'changed'}:files.get(path),
                getResolvedModuleFromModuleSpecifier:node=>({resolvedModule:node.text==='react'
                    ?{resolvedFileName:react.path,extension:ts.Extension.Dts,isExternalLibraryImport:true,packageId:{name:'@types/react',version:admitted.typingPackages.policy.pins['@types/react'],subModuleName:'index.d.ts'}}
                    :{resolvedFileName:control==='barrel_edge_path'?records.barrel.path+'x':records.barrel.path,
                        extension:control==='barrel_edge_extension'?ts.Extension.Tsx:ts.Extension.Ts,
                        isExternalLibraryImport:control==='barrel_edge_external',...(control==='barrel_edge_package'?{packageId:{name:'foreign'}}:{})}})};
            if(control==='barrel_edge_kind')files.get(records.barrel.path).scriptKind=ts.ScriptKind.TSX;
            observeReactProgram(ts,program,{sourcePath:records.component.path,dtoPath:joined.auxiliary_virtual_path,
                sourceHostSha256:records.component.host_sha256,dtoHostSha256:admitted.binding.auxiliary_host_sha256,
                entrySource:records.entry,componentSource:records.component,barrelSource:records.barrel,
                pins:admitted.typingPackages.policy.pins,
                packageRows:admitted.typingPackages.entries.map(row=>({...row,role:'package:'+row.package+'/'+row.member}))});
            fail('barrel_edge_control_not_refused');
        }
        if(control==='source_snapshot'){
            admitted.sourceInputs.component.raw[0]^=1;sourceRecords(admitted,policies.joined);
            fail('two_source_snapshot_not_refused');
        }
        if(control.startsWith('two_edge_')){
            const joined=policies.joined,records=sourceRecords(admitted,joined);
            const component=ts.createSourceFile(records.component.path,admitted.sourceInputs.component.text.replace(/^\ufeff/,''),ts.ScriptTarget.Latest,true);
            const entry=ts.createSourceFile(records.entry.path,admitted.text.replace(/^\ufeff/,''),ts.ScriptTarget.Latest,true);
            const dto=ts.createSourceFile(joined.auxiliary_virtual_path,admitted.auxiliaryRaw.toString('utf8'),ts.ScriptTarget.Latest,true);
            const react=admitted.typingPackages.entries.find(row=>row.package==='@types/react'&&row.member==='index.d.ts');
            let componentReads=0;
            const program={getSourceFile:path=>path===records.component.path?(++componentReads===1||control!=='two_edge_text'?component:{...component,text:'changed'})
                :path===records.entry.path?entry:path===joined.auxiliary_virtual_path?dto
                :path===react.path?{fileName:react.path,isDeclarationFile:true,text:react.raw.toString('utf8')}:null,
                getResolvedModuleFromModuleSpecifier:node=>({resolvedModule:node.text==='react'
                    ?{resolvedFileName:react.path,extension:ts.Extension.Dts,isExternalLibraryImport:true,packageId:{name:'@types/react',version:admitted.typingPackages.policy.pins['@types/react'],subModuleName:'index.d.ts'}}
                    :{resolvedFileName:control==='two_edge_path'?records.component.path+'x':records.component.path,
                        extension:control==='two_edge_extension'?ts.Extension.Dts:ts.Extension.Tsx,
                        isExternalLibraryImport:control==='two_edge_external',...(control==='two_edge_package'?{packageId:{name:'foreign'}}:{})}})};
            observeReactProgram(ts,program,{sourcePath:records.component.path,dtoPath:joined.auxiliary_virtual_path,
                sourceHostSha256:records.component.host_sha256,dtoHostSha256:admitted.binding.auxiliary_host_sha256,
                entrySource:records.entry,componentSource:records.component,pins:admitted.typingPackages.policy.pins,
                packageRows:admitted.typingPackages.entries.map(row=>({...row,role:'package:'+row.package+'/'+row.member}))});
            fail('two_edge_control_not_refused');
        }
        if(control.startsWith('dom_owner_')){
            const source=ts.createSourceFile(policies.joined.compiler_virtual_path,admitted.text,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
            const fn=source.statements[2],jsx=fn.body.statements[0].expression,arrow=jsx.attributes.properties[1].initializer.expression;
            const parameter=arrow.parameters[0],value=arrow.body.statements[0].declarationList.declarations[0].initializer;
            const raw=libraries.find(row=>row.path.endsWith('/lib.dom.d.ts')).raw;
            const dom={isDeclarationFile:true,text:raw.toString('utf8')},reactFile={},globalFile={},foreign={};
            const declaration=(file,parent)=>({getSourceFile:()=>file,parent:{name:{text:parent}}});
            const symbol=(name,file,parent)=>({name,declarations:[declaration(file,parent)]});
            const attribute=symbol('onChange',reactFile,'InputHTMLAttributes'),target=symbol('currentTarget',reactFile,'BaseSyntheticEvent');
            const property=symbol('value',dom,'HTMLInputElement');
            const input=symbol('HTMLInputElement',dom,'');input.declarations.push(declaration(globalFile,''));
            const event={flags:ts.TypeFlags.Object,symbol:symbol('ChangeEvent',reactFile,'')};
            const inputType={symbol:input},targetType={},attributesType={},stringType={flags:ts.TypeFlags.String};
            let contextualEvent=event,propertyAtType=property,domSource=dom;
            if(control==='dom_owner_any')event.flags=ts.TypeFlags.Any;
            if(control==='dom_owner_context')contextualEvent={...event};
            if(control==='dom_owner_attribute')attribute.declarations=[declaration(foreign,'')];
            if(control==='dom_owner_target')target.declarations=[declaration(foreign,'')];
            if(control==='dom_owner_value')property.declarations=[declaration(reactFile,'HTMLInputElement')];
            if(control==='dom_owner_interface')input.declarations.push(declaration(foreign,''));
            if(control==='dom_owner_empty_global')input.declarations=[declaration(globalFile,'')];
            if(control==='dom_owner_symbol')propertyAtType={...property};
            if(control==='dom_owner_string')stringType.flags=ts.TypeFlags.Any;
            if(control==='dom_owner_dom_text')dom.text='changed';
            if(control==='dom_owner_dom_missing')domSource=null;
            const checker={
                getContextualType:node=>node===jsx.attributes?attributesType:node===arrow?{}:null,
                getPropertyOfType:(type,name)=>type===attributesType&&name==='onChange'?attribute
                    :type===event&&name==='currentTarget'?target:type===targetType&&name==='value'?propertyAtType:null,
                getSymbolAtLocation:node=>node===value.name?property:node===value.expression.name?target:null,
                getNonNullableType:type=>type,getSignaturesOfType:()=>[{parameters:[{}]}],
                getTypeOfSymbolAtLocation:()=>contextualEvent,
                getTypeAtLocation:node=>node===parameter.name?event:node===value.expression?targetType:node===value?stringType:null,
                getTypeArguments:()=>[inputType]};
            const observed=observeDomProgram(ts,{getSourceFile:()=>domSource},checker,fn,
                {file:reactFile,row:{role:'package:@types/react/index.d.ts',host_sha256:sha('modeled-react')}},
                globalFile,{role:'package:@types/react/global.d.ts'},
                {componentContract:policies.joined.component_contract,domLibrary:{...policies.image.dom_library,path:policies.image.dom_library.virtual_path}});
            if(control!=='dom_owner_valid')fail('dom_owner_control_did_not_refuse');
            return {status:'DOM_OWNER_MODELED_ONLY',observation:observed,real_compiler_invocations:0,raw_image_persisted:false};
        }
        // Compiler package metadata controls belong to admission below, even
        // when the fixture also carries React typing-package context.
        const typingPackageControl=control.startsWith('package_')
            && control!=='package_name' && control!=='package_version';
        if(typingPackageControl||control.startsWith('dom_')){
            let typing=admitted.typingPackages;const joined=policies.joined,image=policies.image;
            if(!typing)fail('package_unit_context_missing');
            if(control==='package_realpath'||control==='package_foreign_realpath'){
                return withFilesystem(ts,rowsFor(admitted,joined,libraries,metadata),image.limits,ctx=>{
                    const candidate=control==='package_realpath'?path.dirname(ctx.mapping.rows.find(row=>row.role==='package:@types/react/index.d.ts').physical):path.join(ctx.scratch,'foreign');
                    if(ctx.realpath(candidate)!==candidate)fail('package_realpath_changed');
                    return {status:'PACKAGE_REALPATH_MODELED_ONLY',real_compiler_invocations:0,raw_image_persisted:false};
                },control==='package_foreign_realpath'?ctx=>fs.mkdirSync(path.join(ctx.scratch,'foreign')):undefined,true);
            }
            if(control==='package_snapshot')typing={...typing,entries:typing.entries.map((entry,i)=>i?entry:{...entry,raw:Buffer.from('changed'),raw_sha256:sha('changed')})};
            const entries=[{path:joined.compiler_virtual_path,role:'source',raw:admitted.raw},
                {path:joined.auxiliary_virtual_path,role:'auxiliary',raw:admitted.auxiliaryRaw},
                {path:joined.config_virtual_path,role:'auxiliary',raw:admitted.configRaw},
                ...typing.entries.map(row=>({path:row.path,role:'auxiliary',raw:row.raw})),
                ...libraries.map(row=>({...row,path:metadata.policy.library_virtual_root+'/'+path.posix.basename(row.path)}))];
            if(control==='package_removed')entries.splice(3,1);
            if(control==='package_changed')entries[3]={...entries[3],raw:Buffer.from('changed')};
            const domIndex=entries.findIndex(row=>row.path===image.dom_library?.virtual_path);
            if(control==='dom_removed')entries.splice(domIndex,1);
            if(control==='dom_changed')entries[domIndex]={...entries[domIndex],raw:Buffer.from('changed')};
            if(control==='dom_role')entries[domIndex]={...entries[domIndex],role:'auxiliary'};
            const binding={atlas_snapshot_id:admitted.binding.atlas_snapshot_id,atlas_sha256:admitted.binding.atlas_sha256,
                producer_run_id:admitted.binding.invocation_id,policy_sha256:digest(image),compiler_version:ts.version,
                compiler_source_sha256:admitted.binding.compiler_source_sha256,source_sha256:{[joined.compiler_virtual_path]:admitted.binding.source_sha256},
                compiler_metadata_sha256:metadata.expected.raw_sha256,compiler_metadata_policy_sha256:metadata.expected.policy_sha256,
                source_handoff_sha256:admitted.bindingSha256};
            const fake=Object.create(ts);let probes=0;
            Object.defineProperty(fake,'createProgram',{value:options=>{
                for(const entry of typing.entries){if(!options.host.fileExists(entry.path))fail('package_false_absence');probes++;}
                if(options.host.fileExists(joined.compiler_virtual_path.replace('entry.tsx','missing.ts')))fail('package_false_presence');
                if(control==='package_unadmitted')options.host.readFile(typing.entries.find(row=>row.kind==='license_notice').path);
                return {getSourceFiles:()=>[]};
            }});
            Object.defineProperty(fake,'getPreEmitDiagnostics',{value:()=>[]});
            const instance=createExperiment(fake,{namespace_complete:true,entries},binding,image,metadata,typing);
            try{instance.replay(binding);}finally{instance.dispose();}
            if(control!=='package_namespace'&&control!=='dom_namespace')fail('package_control_did_not_refuse');
            return {status:'PACKAGE_NAMESPACE_MODELED_ONLY',present_probes:probes,missing_probe:false,
                real_compiler_invocations:0,raw_image_persisted:false};
        }
        if (control.startsWith('observation_')) {
            const compiler = libraries.map(row=>({...row,path:metadata.policy.library_virtual_root+'/'+path.posix.basename(row.path)}));
            const source=admitted.binding,joined=policies.joined;
            const sources=sourceRecords(admitted,joined);
            if(control==='observation_unbound')metadata.raw[0]^=1;
            const prepared=prepareCompilerInputObservation(compiler,admitted.configRaw,{
                source_path:joined.compiler_virtual_path,source_host_sha256:sha(admitted.text.replace(/^\ufeff/,'')),
                source_bytes:admitted.raw.length,handoff_sha256:admitted.bindingSha256,
                ...(sources?{sources}:{}),
                declaration:{path:joined.auxiliary_virtual_path,raw:admitted.auxiliaryRaw,atlas_context_sha256:source.auxiliary_context_sha256},
                config:{path:joined.config_virtual_path,atlas_context_sha256:source.config_context_sha256},
                ...(admitted.jsxRaw ? {jsx_declaration:{path:joined.jsx_virtual_path,raw:admitted.jsxRaw,atlas_context_sha256:source.jsx_context_sha256}} : {}),
                ...(admitted.typingPackages?{typing_packages:admitted.typingPackages}:{})},policies,metadata);
            const total=(sources?Object.values(admitted.sourceInputs).reduce((n,row)=>n+row.raw.length,0):admitted.raw.length)+admitted.auxiliaryRaw.length+admitted.configRaw.length+metadata.raw.length+(admitted.jsxRaw?.length||0)+(admitted.typingPackages?.raw_bytes||0)
                +compiler.reduce((n,row)=>n+row.raw.length,0);
            const snapshot={image_sha256:sha('modeled_image'),envelope_sha256:sha('modeled_envelope'),file_count:compiler.length+4+(sources?Object.keys(sources).length-1:0)+(admitted.jsxRaw?1:0)+(admitted.typingPackages?.entries.length||0),content_bytes:total};
            const result={status:'DIAGNOSTIC_EXPERIMENT_NOT_ADMITTED',...flags,image_sha256:snapshot.image_sha256,
                envelope_sha256:snapshot.envelope_sha256,metrics:{content_bytes:total},source_files:[
                    {path_sha256:sha(joined.compiler_virtual_path),text_sha256:sha(admitted.text.replace(/^\ufeff/,''))},
                    {path_sha256:sha(joined.auxiliary_virtual_path),text_sha256:source.auxiliary_host_sha256},
                    {path_sha256:sha(compiler[0].path),text_sha256:sha(compiler[0].raw.toString('utf8').replace(/^\ufeff/,''))}],
                host_calls:[{operation:'readFile',path_sha256:sha(joined.auxiliary_virtual_path)},
                    {operation:'readFile',path_sha256:sha(compiler[0].path)}]};
            if(sources){
                for(const [key,row] of Object.entries(sources))if(key!=='entry')result.source_files.push({path_sha256:sha(row.path),text_sha256:row.host_sha256});
                for(const row of Object.values(sources))result.host_calls.push({operation:'readFile',path_sha256:sha(row.path)});
            }
            if(admitted.jsxRaw){
                result.source_files.push({path_sha256:sha(joined.jsx_virtual_path),text_sha256:source.jsx_host_sha256});
                result.host_calls.push({operation:'readFile',path_sha256:sha(joined.jsx_virtual_path)});
            }
            if(admitted.typingPackages)for(const entry of admitted.typingPackages.entries.filter(row=>admitted.typingPackages.policy.required_program_paths.includes(row.path.replace('/__sage_ts_image__/project/','')))){
                result.source_files.push({path_sha256:sha(entry.path),text_sha256:entry.host_sha256});
                result.host_calls.push({operation:'readFile',path_sha256:sha(entry.path)});
            }
            if(control==='observation_valid')return {status:'METADATA_MODELED_OBSERVATION',observation:prepared.project(result,snapshot),real_compiler_invocations:0,raw_image_persisted:false};
            const observedSource=sources?.barrel||sources?.component;
            if(control==='observation_source_missing')result.source_files=result.source_files.filter(row=>row.path_sha256!==sha(observedSource.path));
            else if(control==='observation_source_read')result.host_calls=result.host_calls.filter(row=>row.path_sha256!==sha(observedSource.path));
            else if(control==='observation_source_text')result.source_files.find(row=>row.path_sha256===sha(observedSource.path)).text_sha256=sha('changed');
            else if(control==='observation_jsx_missing'||control==='observation_package_missing')result.source_files.pop();
            else if(control==='observation_jsx_read'||control==='observation_package_read')result.host_calls.pop();
            else if(control==='observation_jsx_text'||control==='observation_package_text')result.source_files[result.source_files.length-1].text_sha256=sha('changed');
            else if(control==='observation_count')snapshot.file_count--;
            else if(control==='observation_bytes')snapshot.content_bytes--;
            else if(control==='observation_program')result.source_files.push({path_sha256:sha(metadata.policy.metadata_virtual_path),text_sha256:metadata.expected.host_sha256});
            else fail('compiler_metadata_unknown_control');
            prepared.project(result,snapshot);
            fail('compiler_metadata_control_not_refused');
        }
        if (control==='comparison') {
            const arm={status:'COMPLETE_FINITE_ARM',...flags,compiler_invocations:1,cached_resolution_reads:1,
                inputs:[],roots:[],options:{},diagnostics:[],checked:[],edge:{},metadata_identity:{raw_sha256:'a'}};
            const other={...arm,metadata_identity:{raw_sha256:'b'}};
            compareArms(arm,other);
            fail('compiler_metadata_control_not_refused');
        }
        if (control==='snapshot') {
            const owned=admitCompilerPackageMetadata(ts,value,owner), original=sha(owned.raw);
            value.raw.fill(0);value.policy.default_enabled=true;value.expected.raw_sha256='0'.repeat(64);
            if(sha(owned.raw)!==original || owned.policy.default_enabled!==false || owned.expected.raw_sha256!==original)
                fail('compiler_metadata_snapshot_changed');
            return {status:'METADATA_MODELED_OWNED_COPY',real_compiler_invocations:0,raw_image_persisted:false};
        }
        if (control==='copy_tamper' || control==='copy_missing') {
            withFilesystem(ts,rowsFor(admitted,policies.joined,libraries,metadata),policies.image.limits,ctx=>{
                ctx.readFile(ctx.mapping.rows.find(row=>row.role==='compiler_metadata').physical);
            },ctx=>{
                const filename=ctx.mapping.rows.find(row=>row.role==='compiler_metadata').physical;
                if(control==='copy_tamper')fs.writeFileSync(filename,'changed');else fs.unlinkSync(filename);
            },true);
            fail('compiler_metadata_control_not_refused');
        }
        if (['old_role','library_layout','budget'].includes(control)) {
            const entryPaths = libraries.map(row=>({...row,path:metadata.policy.library_virtual_root+'/'+path.posix.basename(row.path)}));
            if(control==='library_layout') {
                createDeclarationExperiment(ts,admitted,policies,
                    [{...libraries[0],path:'/__sage_ts_image__/compiler/../lib.bad.d.ts'}],metadata);
            } else {
                const image=JSON.parse(canonical(policies.image));
                if(control==='budget')image.limits.compiler_files=libraries.length;
                const binding={atlas_snapshot_id:admitted.binding.atlas_snapshot_id,atlas_sha256:admitted.binding.atlas_sha256,
                    producer_run_id:admitted.binding.invocation_id,policy_sha256:digest(image),
                    compiler_version:admitted.binding.compiler_version,compiler_source_sha256:admitted.binding.compiler_source_sha256,
                    source_sha256:{[policies.joined.compiler_virtual_path]:admitted.binding.source_sha256}};
                const entries=[{path:policies.joined.compiler_virtual_path,role:'source',raw:admitted.raw},
                    {path:policies.joined.auxiliary_virtual_path,role:'auxiliary',raw:admitted.auxiliaryRaw},
                    {path:policies.joined.config_virtual_path,role:'auxiliary',raw:admitted.configRaw},
                    ...(control==='budget'?entryPaths:libraries)];
                if(control==='old_role')entries.push({path:metadata.policy.metadata_virtual_path,role:'compiler_metadata',raw:metadata.raw});
                else Object.assign(binding,{compiler_metadata_sha256:metadata.expected.raw_sha256,
                    compiler_metadata_policy_sha256:metadata.expected.policy_sha256,source_handoff_sha256:admitted.bindingSha256});
                const instance=createExperiment(ts,{namespace_complete:true,entries},binding,image,control==='budget'?metadata:null);
                instance.dispose();
            }
            fail('compiler_metadata_control_not_refused');
        }
        if (control==='raw')value.raw[0]^=1;
        else if(control==='raw_hash')value.expected.raw_sha256='0'.repeat(64);
        else if(control==='host_hash')value.expected.host_sha256='0'.repeat(64);
        else if(control==='bytes')value.expected.bytes=true;
        else if(control==='policy_hash')value.expected.policy_sha256='0'.repeat(64);
        else if(control==='handoff')value.expected.source_handoff_sha256='0'.repeat(64);
        else if(control==='compiler_hash')value.expected.compiler_source_sha256='0'.repeat(64);
        else if(control==='compiler_version')value.expected.compiler_version='0.0.0';
        else if(control==='activation')value.policy.production_enabled=true;
        else if(control==='path')value.policy.metadata_virtual_path='/__sage_ts_image__/compiler/other.json';
        else if(control==='root')value.policy.library_virtual_root='/__sage_ts_image__/compiler/../lib';
        else if(control==='role')value.policy.metadata_role='source';
        else if(control==='cap')value.policy.max_metadata_bytes++;
        else if(control==='unknown_policy')value.policy.extra=true;
        else if(control==='unknown_input')value.extra=true;
        else if(control==='unknown_expected')value.expected.extra=true;
        else if(control==='oversize')updateRaw(Buffer.alloc(value.policy.max_metadata_bytes+1,32));
        else if(control==='encoding')updateRaw(Buffer.from([255]));
        else if(control==='json')updateRaw(Buffer.from('{'));
        else if(control==='duplicate')updateRaw(Buffer.from('{"name":"typescript","name":"typescript","version":'+JSON.stringify(ts.version)+'}'));
        else if(control==='package_name')updateRaw(Buffer.from(JSON.stringify({name:'foreign',version:ts.version})));
        else if(control==='package_version')updateRaw(Buffer.from(JSON.stringify({name:'typescript',version:'0.0.0'})));
        else fail('compiler_metadata_unknown_control');
        admitCompilerPackageMetadata(ts,value,owner);
        fail('compiler_metadata_control_not_refused');
    }catch(error){
        return {status:'METADATA_MODELED_REFUSAL',code:error.code||'compiler_metadata_unexpected_error',
            temporary_copy_removed:error.calibration_cleanup??null,real_compiler_invocations:0,raw_image_persisted:false};
    }
}

function runLocationControl(ts,admitted,policies,control){
    const text=admitted.text.replace(/^\ufeff/,'');
    const source=ts.createSourceFile(policies.joined.compiler_virtual_path,text,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
    const fn=source.statements[2],attribute=fn.body.statements[0].expression.attributes.properties[1];
    const param=attribute.initializer.expression.parameters[0],value=attribute.initializer.expression.body.statements[0].declarationList.declarations[0].initializer;
    const nodes={event_attribute:attribute.name,parameter_declaration:param.name,parameter_reference:value.expression.expression,
        current_target_member:value.expression.name,value_member:value.name,value_access:value};
    const binding={parent_handoff_sha256:admitted.bindingSha256,source_raw_sha256:sha(admitted.raw),source_host_sha256:sha(text),
        leading_bom_utf16_units_removed:admitted.text.startsWith('\ufeff')?1:0};
    const symbol={declarations:[param]};
    const checker={getSymbolAtLocation:node=>control==='parameter_symbol'&&node===nodes.parameter_reference?{}:symbol};
    if(control==='source')value.name.getSourceFile=()=>({text});
    if(control==='missing')delete nodes.value_member;
    if(control==='extra')nodes.extra=value;
    if(control==='host_hash')binding.source_host_sha256=sha('changed');
    if(control==='bom_type')binding.leading_bom_utf16_units_removed=true;
    if(['negative','empty','fractional','outside','surrogate','containment'].includes(control)){
        const node=value.name,position=control==='surrogate'?text.indexOf('😀')+1
            :control==='negative'?-1:control==='fractional'?0.5:control==='outside'?text.length+1
            :control==='containment'?param.name.getStart(source):node.getStart(source);
        const end=node.getEnd();
        node.getStart=()=>position;
        node.getEnd=()=>control==='empty'?position:control==='outside'?position+1:control==='containment'?position+1:end;
    }
    try{
        const observation=observeDomLocations(ts,checker,source,nodes,policies.joined.source_location_contract,binding);
        if(control!=='valid')fail('location_control_did_not_refuse');
        return {status:'DOM_LOCATION_MODELED_ONLY',observation,real_compiler_invocations:0,raw_image_persisted:false};
    }catch(error){return {status:'DOM_LOCATION_MODELED_REFUSAL',code:error.code||'location_unit_unexpected_error',
        real_compiler_invocations:0,raw_image_persisted:false};}
}

module.exports={runCalibration,runLocationControl};

'use strict';
// Disabled one/set declaration memory experiments. No IO, compiler/image factory,
// namespace admission or production consumer. Existing raw stager stays intact.
const { performance } = require('node:perf_hooks');
const { types } = require('node:util');
const { _copyDecodeOwnedText, _finishOwnedText, hashObject, ImageRefusal } = require('./ts_input_image_experiment.cjs');
const { createCompilerStreamStager, StreamStagerRefusal } = require('./compiler_stream_stager.cjs');

class DeclarationTextRefusal extends Error {
    constructor(code) { super(code); this.code = code; }
}
const refuse = code => { throw new DeclarationTextRefusal(code); };
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const sha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const exact = (value,keys) => object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value,key));
const flags = () => Object.fromEntries(['actual_transfer','raw_content_verified','host_text_verified','source_freshness_verified',
    'snapshot_bound','loaded_code_attested','native_execution_attested','image_admitted','production_enabled','semantic_lineage','cache_eligible'].map(key => [key,false]));
// Only opted-in v3 factory objects enter this module instance's private registry.
const registeredHandoffOwners = new WeakMap();

function _borrowPreparedCompilerTextSet(owner,expected,callback) {
    const registered = registeredHandoffOwners.get(owner);
    if (!registered) refuse('text_preparation_order');
    return registered.borrow(expected,callback);
}

// Clock-free cleanup through the actual producer entry, never caller methods.
function _disposePreparedCompilerTextSet(owner) {
    const registered = registeredHandoffOwners.get(owner);
    if (!registered) return false;
    registered.dispose();
    return true;
}

// Closed flat policy-definition reader, not a general schema implementation.
// Constants/keys come from the dedicated schema; unsupported assertions refuse.
function policyFor(value,schema,multiple,handoff) {
    const def = schema?.$defs?.policy;
    if (!exact(def,['type','additionalProperties','required','properties']) || def.type !== 'object'
        || def.additionalProperties !== false || !Array.isArray(def.required) || !object(def.properties)
        || !exact(value,def.required) || !exact(def.properties,def.required)) refuse('text_preparation_policy_invalid');
    for (const key of def.required) {
        const rule = def.properties[key], item = value[key];
        if (!object(rule) || Object.keys(rule).some(name => !['type','const','pattern','minLength'].includes(name))) refuse('text_preparation_policy_invalid');
        if (!['string','boolean','array'].includes(rule.type)
            || (rule.type === 'array' ? !Array.isArray(item) : typeof item !== rule.type)) refuse('text_preparation_policy_invalid');
        if (Object.hasOwn(rule,'const') && hashObject(item) !== hashObject(rule.const)) refuse('text_preparation_policy_invalid');
        if (rule.minLength !== undefined && (typeof item !== 'string' || item.length < rule.minLength)) refuse('text_preparation_policy_invalid');
        if (rule.pattern !== undefined) {
            if (rule.pattern !== '^[a-f0-9]{64}$' || !sha(item)) refuse('text_preparation_policy_invalid');
        }
    }
    if (value.version !== (handoff ? 'v3-private-compiler-declaration-text-preparation' : multiple ? 'v2-private-compiler-declaration-text-preparation' : 'v1-private-compiler-declaration-text-preparation')
        || value.model !== (handoff ? 'synthetic_registered_declaration_text_set_v1' : multiple ? 'synthetic_declaration_text_set_v1' : 'single_synthetic_declaration_text_v1')
        || ['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].some(key => value[key] !== false)
        || value.schema_sha256 !== hashObject(schema)) refuse('text_preparation_policy_invalid');
    return value;
}

function preparedOwner(records,refs,descriptor,deadline,now,multiple,handoff) {
    let held = records, state = 'READY', code = '';
    records = null;
    const forget = () => {
        if (held !== null) held.length = 0;
        held = null;
        if (handoff) registeredHandoffOwners.delete(owner);
    };
    function check() {
        const time = now();
        if (!Number.isFinite(time) || time >= deadline) refuse('text_preparation_deadline');
    }
    const owner = {
        consume(callback) {
            let settled = false;
            try {
                check();
                if (state !== 'READY' || typeof callback !== 'function' || types.isAsyncFunction(callback)) refuse('text_preparation_order');
                state = 'BORROWING';
                const rows = Object.freeze(held.map(row => Object.freeze({name:row.name,...row.record})));
                const result = callback(multiple ? rows : rows[0]);
                if (result && typeof result.then === 'function') refuse('text_preparation_order');
                if (state !== 'BORROWING' || held === null) refuse('text_preparation_order');
                check(); settled = true; return result;
            } catch (error) {
                code = error instanceof DeclarationTextRefusal ? error.code : 'text_preparation_internal';
                throw error;
            } finally { forget(); state = settled ? 'DISPOSED' : 'ABORTED'; }
        },
        snapshot() {
            return {version:descriptor.version,status:descriptor.snapshot_status,owner:handoff ? 'handoff_set' : multiple ? 'prepared_set' : 'prepared',state,code,...refs,
                declaration_text_pins_verified:held !== null,retained_text_records:held === null ? 0 : held.length,
                ...(handoff ? {handoff_registered:registeredHandoffOwners.has(owner)} : {}),
                raw_stager_state:'DISPOSED',whole_image_within_limit:null,peak_rss_bytes:null,...flags()};
        },
        dispose() { forget(); state = 'DISPOSED'; },
        toJSON() { throw new TypeError('prepared_compiler_text_not_serializable'); },
        [Symbol.for('nodejs.util.inspect.custom')]() { return '<private prepared compiler text ' + state + '>'; }
    };
    if (handoff) {
        // Capture the actual factory-created operation now; never look up a
        // caller's public method when the private borrow is requested later.
        const consume = owner.consume, expectedKeys = Object.keys(refs);
        registeredHandoffOwners.set(owner,Object.freeze({dispose:owner.dispose,
            borrow(expectedInput,callback) {
                return consume(rows => {
                    if (typeof callback !== 'function' || types.isAsyncFunction(callback)) refuse('text_preparation_order');
                    if (!object(expectedInput)) refuse('text_preparation_expected_invalid');
                    const properties = Object.getOwnPropertyDescriptors(expectedInput);
                    if (Reflect.ownKeys(properties).length !== expectedKeys.length || expectedKeys.some(key =>
                        !Object.hasOwn(properties,key) || !Object.hasOwn(properties[key],'value') || !sha(properties[key].value))) refuse('text_preparation_expected_invalid');
                    const expected = Object.freeze(Object.fromEntries(expectedKeys.map(key => [key,properties[key].value])));
                    if (expectedKeys.some(key => expected[key] !== refs[key])) refuse('text_preparation_identity');
                    // Proxy traps on the metadata copy can reenter/dispose the
                    // owner. Such an attempt must poison this borrow too.
                    if (state !== 'BORROWING' || held === null) refuse('text_preparation_order');
                    check();
                    return callback(rows);
                });
            }
        }));
    }
    return Object.freeze(owner);
}

function createTextPreparation(expectedInput,manifestInput,profilesInput,transportSchemaInput,textSchemaInput,options,multiple,handoff=false) {
    const now = options?.now || (() => performance.now()), deadline = options?.deadline, started = now();
    let held, descriptor, manifest, stager, refs, metadataLimit, imageLimit, rawBytesLowerBound;
    try {
        const first = profilesInput.private_compiler_stream_layout;
        const ceiling = profilesInput[first.image_profile].limits.metadata_bytes;
        if (!Number.isSafeInteger(ceiling) || ceiling < 1) refuse('text_preparation_policy_invalid');
        const text = JSON.stringify({expected:expectedInput,manifest:manifestInput,profiles:profilesInput,transportSchema:transportSchemaInput,textSchema:textSchemaInput});
        if (Buffer.byteLength(text) > ceiling) refuse('text_preparation_budget');
        held = JSON.parse(text);
        const {profiles,expected,transportSchema,textSchema} = held;
        descriptor = policyFor(profiles[handoff ? 'private_compiler_declaration_text_handoff_preparation' : multiple ? 'private_compiler_declaration_text_set_preparation' : 'private_compiler_declaration_text_preparation'],textSchema,multiple,handoff);
        const layout = profiles.private_compiler_stream_layout, image = profiles[layout.image_profile].limits;
        metadataLimit = image.metadata_bytes; imageLimit = image.image_bytes;
        manifest = held.manifest;
        if (descriptor.image_profile !== layout.image_profile || descriptor.image_profile !== 'private_input_image_experiment'
            || descriptor.stager_profile !== 'private_compiler_stream_js_stager') refuse('text_preparation_policy_invalid');
        if (typeof deadline !== 'number' || !Number.isFinite(deadline) || typeof started !== 'number' || !Number.isFinite(started)
            || !(started < deadline && deadline <= started+image.capture_ms)) refuse('text_preparation_deadline');
        if (!object(manifest) || hashObject(manifest) !== expected.manifest_sha256 || manifest.mapping_root !== layout.mapping_root
            || !Array.isArray(manifest.files) || (multiple ? manifest.files.length < 1 : manifest.files.length !== 1)
            || Object.keys(expected).some(key => key !== 'manifest_sha256' && manifest[key] !== expected[key])) refuse('text_preparation_expected_invalid');
        const capture = profiles[layout.capture_profile];
        if (multiple) {
            const limits = [image.compiler_files,image.compiler_file_bytes,image.compiler_bytes,image.image_bytes,
                capture.max_files,capture.max_file_bytes,capture.max_total_bytes];
            if (limits.some(n => !Number.isSafeInteger(n) || n < 1)) refuse('text_preparation_policy_invalid');
            if (manifest.files.length > Math.min(image.compiler_files,capture.max_files)) refuse('text_preparation_budget');
        }
        const folded = multiple ? new Set() : null;
        let total = 0;
        for (const [index,row] of manifest.files.entries()) {
            if (!exact(row,['name','bytes','raw_sha256','host_text_sha256']) || !/^lib[A-Za-z0-9_.-]*\.d\.ts$/.test(row.name)
                || !sha(row.raw_sha256) || !sha(row.host_text_sha256) || !Number.isSafeInteger(row.bytes) || row.bytes < 0) refuse('text_preparation_expected_invalid');
            if (row.bytes > Math.min(image.compiler_file_bytes,image.compiler_bytes,image.image_bytes,capture.max_file_bytes,capture.max_total_bytes)) refuse('text_preparation_budget');
            if (multiple) {
                if (index && manifest.files[index-1].name >= row.name || folded.has(row.name.toLowerCase())) refuse('text_preparation_expected_invalid');
                folded.add(row.name.toLowerCase()); total += row.bytes;
                if (!Number.isSafeInteger(total) || total > Math.min(image.compiler_bytes,image.image_bytes,capture.max_total_bytes)) refuse('text_preparation_budget');
            }
        }
        rawBytesLowerBound = total;
        refs = Object.fromEntries(['context_sha256','inventory_sha256','manifest_sha256'].map(key => [key,expected[key]]));
        Object.assign(refs,{preparation_policy_sha256:hashObject(descriptor),preparation_schema_sha256:hashObject(textSchema),image_policy_sha256:hashObject(profiles[layout.image_profile])});
        if (handoff) {
            const def = textSchema.$defs[descriptor.borrow_expected_definition], keys = Object.keys(refs);
            if (!exact(def,['type','additionalProperties','required','properties']) || def.type !== 'object'
                || def.additionalProperties !== false || !Array.isArray(def.required) || def.required.length !== keys.length
                || !keys.every(key => def.required.includes(key)) || !exact(def.properties,keys)
                || keys.some(key => !exact(def.properties[key],['type','pattern']) || def.properties[key].type !== 'string'
                    || def.properties[key].pattern !== '^[a-f0-9]{64}$')) refuse('text_preparation_policy_invalid');
        }
        stager = createCompilerStreamStager(expected,profiles,transportSchema,{now,deadline});
        const time = now();
        if (!Number.isFinite(time) || time >= deadline) refuse('text_preparation_deadline');
    } catch (error) {
        stager?.dispose();
        if (error instanceof TypeError) refuse('text_preparation_policy_invalid');
        throw error;
    }
    let state = 'RECEIVING', code = '', published = false;
    const clear = () => { stager?.dispose(); stager = held = manifest = null; };
    function check() {
        const time = now();
        if (!Number.isFinite(time) || time >= deadline) refuse('text_preparation_deadline');
        if (['TRANSFERRED','ABORTED','DISPOSED'].includes(state)) refuse('text_preparation_order');
    }
    function operate(action,payload) {
        let candidate = null, ready = null, settled = false, preparedMetadata = 0;
        try {
            check(); let result;
            if (action === 'feed') result = stager.feed(payload);
            else if (action === 'release') result = stager.release_credit(payload);
            else if (action === 'eof') { result = stager.end_input(); state = 'RAW_COMPLETE'; }
            else if (action === 'prepare') {
                if (state !== 'RAW_COMPLETE' || stager.snapshot().state !== 'COMPLETE') refuse('text_preparation_order');
                state = 'PREPARING';
                candidate = [];
                stager.consume(rows => {
                    check();
                    if (rows.length !== manifest.files.length) refuse('text_preparation_identity');
                    for (const [index,row] of rows.entries()) {
                        if (index) check();
                        const expected = manifest.files[index];
                        if (row.name !== expected.name) refuse('text_preparation_identity');
                        let phase, record;
                        try {
                            phase = _copyDecodeOwnedText(row.raw); check();
                            record = _finishOwnedText(phase); check();
                        } finally { phase?.dispose(); }
                        if (record.size_bytes !== expected.bytes || record.raw_sha256 !== expected.raw_sha256
                            || record.host_sha256 !== expected.host_text_sha256) refuse('text_preparation_identity');
                        if (multiple) {
                            preparedMetadata += Buffer.byteLength(JSON.stringify({name:row.name,raw_sha256:record.raw_sha256,
                                parser_sha256:record.parser_sha256,host_sha256:record.host_sha256,size_bytes:record.size_bytes}));
                            if (!Number.isSafeInteger(preparedMetadata) || preparedMetadata > metadataLimit
                                || rawBytesLowerBound+preparedMetadata > imageLimit) refuse('text_preparation_budget');
                        }
                        candidate.push({name:row.name,record});
                    }
                });
                // The stager's post-callback check must settle before any owner escapes.
                check();
                ready = preparedOwner(candidate,refs,descriptor,deadline,now,multiple,handoff);
                check(); candidate = null; state = 'TRANSFERRED'; published = true; clear(); result = ready;
            } else refuse('text_preparation_order');
            if (action !== 'prepare') check();
            settled = true; return result;
        } catch (error) {
            state = 'ABORTED';
            if (error instanceof ImageRefusal) {
                code = error.code === 'encoding_refused' ? 'text_preparation_encoding_refused' : 'text_preparation_input_invalid';
                throw new DeclarationTextRefusal(code);
            }
            if (error instanceof StreamStagerRefusal) { code = 'text_preparation_stager_refused'; throw new DeclarationTextRefusal(code); }
            code = error instanceof DeclarationTextRefusal ? error.code : 'text_preparation_internal';
            throw error;
        } finally {
            if (candidate !== null) candidate.length = 0;
            candidate = null;
            if (!settled) { ready?.dispose(); published = false; clear(); }
        }
    }
    const api = {
        feed: value => operate('feed',value), release_credit: value => operate('release',value),
        end_input: () => operate('eof'), prepare: () => operate('prepare'),
        snapshot() {
            return {version:descriptor.version,status:descriptor.snapshot_status,owner:'gate',state,code,...refs,
                declaration_text_pins_verified:published,retained_text_records:0,raw_stager_state:stager?.snapshot().state || 'DISPOSED',
                ...(handoff ? {handoff_registered:false} : {}),
                whole_image_within_limit:null,peak_rss_bytes:null,...flags()};
        },
        dispose() { clear(); published = false; state = 'DISPOSED'; },
        toJSON() { throw new TypeError('compiler_text_preparation_not_serializable'); },
        [Symbol.for('nodejs.util.inspect.custom')]() { return '<private compiler text gate ' + state + '>'; }
    };
    return Object.freeze(api);
}

function createCompilerDeclarationTextPreparation(expected,manifest,profiles,transportSchema,textSchema,options) {
    return createTextPreparation(expected,manifest,profiles,transportSchema,textSchema,options,false);
}

function createCompilerDeclarationTextSetPreparation(expected,manifest,profiles,transportSchema,textSchema,options) {
    return createTextPreparation(expected,manifest,profiles,transportSchema,textSchema,options,true);
}

function createCompilerDeclarationTextHandoffPreparation(expected,manifest,profiles,transportSchema,textSchema,options) {
    return createTextPreparation(expected,manifest,profiles,transportSchema,textSchema,options,true,true);
}

module.exports = { createCompilerDeclarationTextPreparation, createCompilerDeclarationTextSetPreparation,
    createCompilerDeclarationTextHandoffPreparation, _borrowPreparedCompilerTextSet, _disposePreparedCompilerTextSet, DeclarationTextRefusal };

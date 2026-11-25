'use strict';
// Explicit synthetic stdio endpoint. Only this invocation's stdin supplies data;
// no installation/target/config read, TypeScript load or image/Program consumer.
const crypto = require('node:crypto');
const { performance } = require('node:perf_hooks');
const { createCompilerStreamStager } = require('./compiler_stream_stager.cjs');

// The new policy consumes only a closed flat literal/type subset. Definitions
// own its values; unsupported assertions refuse rather than being ignored.
function textCompositionPolicy(value,schema,hashObject) {
    const def = schema?.$defs?.driver_policy;
    if (!def || Object.keys(def).some(k => !['type','additionalProperties','required','properties'].includes(k))
        || def.type !== 'object' || def.additionalProperties !== false
        || !Array.isArray(def.required) || !value || typeof value !== 'object'
        || Object.keys(value).length !== def.required.length
        || Object.keys(def.properties).length !== def.required.length) throw Error('composition');
    for (const key of def.required) {
        const rule = def.properties[key], item = value[key];
        if (!rule || Object.keys(rule).some(k => !['type','const','minLength','pattern'].includes(k))
            || (rule.type === 'array' ? !Array.isArray(item) : !['string','boolean'].includes(rule.type) || typeof item !== rule.type)
            || Object.hasOwn(rule,'const') && hashObject(item) !== hashObject(rule.const)
            || rule.minLength !== undefined && (typeof item !== 'string' || item.length < rule.minLength)
            || rule.pattern !== undefined && (rule.pattern !== '^[a-f0-9]{64}$' || typeof item !== 'string' || !/^[a-f0-9]{64}$/.test(item))) throw Error('composition');
    }
    if (value.schema_sha256 !== hashObject(schema)) throw Error('composition');
}

async function runMode(textMode,imageMode=false) {
    const cap = Number(process.argv[2]), allowance = Number(process.argv[3]);
    if (!Number.isSafeInteger(cap) || cap <= 0 || !Number.isFinite(allowance) || allowance <= 0) throw Error('bootstrap');
    const start = performance.now();
    let timer, stager, prepared, imageProcess, prefix = Buffer.alloc(0), responseCap, bootstrap, localDeadline;
    const expire = () => { imageProcess?.dispose(); prepared?.dispose(); stager?.dispose(); process.exit(1); };
    timer = setTimeout(expire, allowance);
    async function send(value) {
        const bytes = Buffer.from(JSON.stringify(value) + '\n');
        if (bytes.length > responseCap) throw Error('response');
        await new Promise((resolve,reject) => process.stdout.write(bytes,error => error ? reject(error) : resolve()));
    }
    try {
        for await (const chunk of process.stdin) {
            if (!bootstrap) {
                if (prefix.length + chunk.length > cap) throw Error('bootstrap');
                prefix = Buffer.concat([prefix,chunk]);
                const newline = prefix.indexOf(10);
                if (newline < 0) continue;
                if (newline !== prefix.length-1) throw Error('bootstrap');
                const text = new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(prefix.subarray(0,-1));
                bootstrap = JSON.parse(text); prefix = null;
                const { profiles, schema, expected, remaining_ms: remaining } = bootstrap;
                const descriptor = profiles[imageMode ? 'private_prepared_compiler_image_process' : textMode ? 'private_compiler_text_process' : 'private_compiler_stream_driver'];
                const layout = profiles[descriptor.layout_profile];
                const image = profiles[layout.image_profile].limits;
                const wire = profiles[layout.wire_profile];
                if (bootstrap.kind !== 'bootstrap' || bootstrap.version !== descriptor.version
                    || descriptor.version !== (imageMode ? 'v1-private-synthetic-prepared-base-image-process' : textMode ? 'v1-private-synthetic-compiler-text-process' : 'v1-private-synthetic-compiler-stream-driver')
                    || !Number.isFinite(remaining) || remaining <= 0 || remaining > allowance
                    || cap !== image.metadata_bytes || allowance > image.capture_ms
                    || ['default_enabled','native_execution_enabled','raw_transport_enabled','production_enabled','semantic_lineage','cache_eligible']
                        .some(key => descriptor[key] !== false)) throw Error('bootstrap');
                responseCap = Math.min(image.metadata_bytes,profiles[wire.bridge_profile].max_response_bytes);
                const deadline = Math.min(start+allowance,performance.now()+remaining);
                localDeadline = deadline;
                if (performance.now() >= deadline) throw Error('deadline');
                clearTimeout(timer); timer = setTimeout(expire,deadline-performance.now());
                let textRefs,imageRefs;
                if(imageMode){
                    const {createImageProcess,canonical}=require('./prepared_image_process.cjs');
                    if(!Number.isSafeInteger(remaining)||!Number.isSafeInteger(allowance)||text!==canonical(bootstrap))throw Error('image bootstrap');
                    imageProcess=createImageProcess(bootstrap,{deadline});stager=imageProcess.gate;imageRefs=imageProcess.refs;
                } else if (textMode) {
                    const { createCompilerDeclarationTextSetPreparation } = require('./compiler_declaration_text.cjs');
                    const { hashObject } = require('./ts_input_image_experiment.cjs');
                    const { manifest, composition_schema: composition, preparation_schema: preparation } = bootstrap.text_context;
                    textCompositionPolicy(descriptor,composition,hashObject);
                    stager = createCompilerDeclarationTextSetPreparation(expected,manifest,profiles,schema,preparation,{deadline});
                    const snapshot = stager.snapshot();
                    textRefs = {composition_policy_sha256:hashObject(descriptor),composition_schema_sha256:hashObject(composition),
                        ...Object.fromEntries(['preparation_policy_sha256','preparation_schema_sha256','image_policy_sha256'].map(k => [k,snapshot[k]]))};
                } else stager = createCompilerStreamStager(expected,profiles,schema,{deadline});
                // Keep only the bounded references needed by the final metadata.
                bootstrap = { version:descriptor.version,status:descriptor.result_status,expected,...(textMode ? {textRefs} : {}),...(imageMode?{imageRefs}:{}) };
                await send({kind:'ready',version:bootstrap.version,session_id:expected.session_id,manifest_sha256:expected.manifest_sha256,
                    ...(imageMode?{process_context_sha256:imageRefs.process_context_sha256}:{})});
            } else {
                const ack = stager.feed(chunk);
                if (ack !== null) {
                    await send(ack);
                    stager.release_credit(ack);
                }
            }
        }
        if (!stager) throw Error('incomplete');
        const ack = stager.end_input(); // Actual clean OS EOF, never a frame marker.
        if(imageMode){
            const summary=imageProcess.complete();imageProcess.dispose();imageProcess=null;stager=null;
            if(performance.now()>=localDeadline)throw Error('deadline');
            await send(ack);
            await send({kind:'result',version:bootstrap.version,status:bootstrap.status,
                ...Object.fromEntries(['session_id','manifest_sha256','context_sha256','inventory_sha256'].map(key=>[key,bootstrap.expected[key]])),
                ...summary,node_version:process.version,real_source_transfer:false,raw_content_verified:false,host_text_verified:false,
                source_freshness_verified:false,snapshot_bound:false,native_execution_attested:false,loaded_code_attested:false,image_admitted:false,
                production_enabled:false,semantic_lineage:false,cache_eligible:false,whole_image_within_limit:null,peak_rss_bytes:null});
            if(performance.now()>=localDeadline)throw Error('deadline');
        } else if (textMode) {
            prepared = stager.prepare();
            const summary = prepared.consume(rows => {
                const hash = crypto.createHash('sha256');
                let bytes = 0;
                for (const row of rows) {
                    hash.update(JSON.stringify([row.name,row.size_bytes,row.raw_sha256,row.host_sha256])+'\n');
                    bytes += row.size_bytes;
                }
                return {files_completed:rows.length,raw_bytes_staged:bytes,prepared_text_summary_sha256:hash.digest('hex')};
            });
            const disposed = prepared.snapshot();
            if (disposed.state !== 'DISPOSED' || disposed.retained_text_records !== 0) throw Error('text owner');
            prepared.dispose(); stager.dispose(); prepared = stager = null;
            if (performance.now() >= localDeadline) throw Error('deadline');
            await send(ack);
            await send({kind:'result',version:bootstrap.version,status:bootstrap.status,
                ...Object.fromEntries(['session_id','manifest_sha256','context_sha256','inventory_sha256'].map(key => [key,bootstrap.expected[key]])),
                ...bootstrap.textRefs,...summary,node_version:process.version,
                receiver_state:'DISPOSED',owned_buffer_bytes:0,prepared_state:disposed.state,retained_text_records:0,
                declaration_text_pins_verified:true,raw_bytes_verified_in_model:true,
                real_source_transfer:false,raw_content_verified:false,host_text_verified:false,production_enabled:false,
                native_execution_attested:false,semantic_lineage:false,cache_eligible:false,image_admitted:false,
                source_freshness_verified:false,snapshot_bound:false,loaded_code_attested:false,whole_image_within_limit:null,peak_rss_bytes:null});
            if (performance.now() >= localDeadline) throw Error('deadline');
        } else {
        const complete = stager.snapshot();
        const summary = stager.consume(rows => {
            const hash = crypto.createHash('sha256');
            for (const row of rows) hash.update(JSON.stringify([row.name,row.raw.length,crypto.createHash('sha256').update(row.raw).digest('hex')])+'\n');
            return hash.digest('hex');
        });
        const disposed = stager.snapshot();
        await send(ack);
        await send({kind:'result',version:bootstrap.version,status:bootstrap.status,
            ...Object.fromEntries(['session_id','manifest_sha256','context_sha256','inventory_sha256'].map(key => [key,bootstrap.expected[key]])),
            node_version:process.version,files_completed:complete.files_completed,raw_bytes_staged:complete.raw_bytes_staged,content_summary_sha256:summary,
            receiver_state:disposed.state,owned_buffer_bytes:disposed.owned_buffer_bytes,raw_bytes_verified_in_model:true,
            real_source_transfer:false,raw_content_verified:false,host_text_verified:false,production_enabled:false,
            native_execution_attested:false,semantic_lineage:false,cache_eligible:false});
        }
        await new Promise(resolve => process.stdout.end(resolve));
        if(imageMode&&performance.now()>=localDeadline)throw Error('deadline');
    } finally {
        clearTimeout(timer); imageProcess?.dispose(); prepared?.dispose(); stager?.dispose(); imageProcess = prepared = stager = prefix = bootstrap = null;
    }
}

const run = () => runMode(false);
const runText = () => runMode(true);
const runImage = () => runMode(false,true);

if (require.main === module) (process.argv[4] === '--prepared-base-image' ? runImage() : process.argv[4] === '--declaration-text' ? runText() : run()).catch(() => {
    process.exitCode = 1;
    process.stdin.destroy(); process.stdout.destroy();
    process.stderr.end('compiler_stream_endpoint_refused\n');
});

module.exports = { run, runText, runImage };

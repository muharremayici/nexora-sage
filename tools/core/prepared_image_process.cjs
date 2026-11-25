'use strict';
// Disabled synthetic constructor consumer. No installation/target reader or TS load.
const crypto=require('node:crypto'),{performance}=require('node:perf_hooks');
const {_validateCompilerStreamValue:validate,_canonicalCompilerStreamMetadata:canonical}=require('./compiler_stream_stager.cjs');
const image=require('./ts_input_image_experiment.cjs');
const {createCompilerDeclarationTextHandoffPreparation:createGate,_disposePreparedCompilerTextSet:release}=require('./compiler_declaration_text.cjs');
const hash=value=>crypto.createHash('sha256').update(canonical(value)).digest('hex');
const digest=value=>crypto.createHash('sha256').update(value).digest('hex');
const need=value=>{if(!value)throw Error('synthetic image process context');};
const same=(a,b)=>canonical(a)===canonical(b);
const exact=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)&&same(Object.keys(value).sort(),keys.slice().sort());
const PROFILE='private_prepared_compiler_image_process';

function context(bootstrap) {
    need(exact(bootstrap,['kind','version','expected','profiles','schema','image_context','process_refs','remaining_ms']));
    const {profiles,schema:wireSchema,expected,image_context:ctx}=bootstrap,p=profiles[PROFILE],schema=ctx.composition_schema;
    validate(p,schema.$defs.driver_policy,schema,'image_process_policy');
    validate(ctx,schema.$defs[p.context_definition],schema,'image_process_context');
    need(p.version==='v1-private-synthetic-prepared-base-image-process'&&p.model==='synthetic_prepared_base_image_one_credit_v1'
        &&p.schema_sha256===hash(schema)&&bootstrap.kind==='bootstrap'&&bootstrap.version===p.version
        &&['default_enabled','native_execution_enabled','raw_transport_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>p[k]===false));
    const imagePolicy=profiles[p.image_profile],source=profiles[p.source_profile],bridge=profiles[p.bridge_profile];
    const prep=profiles[p.preparation_profile],ingress=profiles[p.ingress_profile],layout=profiles[p.layout_profile],js=profiles.private_compiler_stream_js_stager;
    const names=[PROFILE,p.layout_profile,p.image_profile,p.source_profile,p.bridge_profile,p.preparation_profile,p.ingress_profile,
        layout.wire_profile,layout.capture_profile,'private_compiler_stream_js_stager',js.stager_profile];
    need(same(Object.keys(profiles).sort(),[...new Set(names)].sort()));
    for(const [name,value] of [['image',imagePolicy],['source',source],['bridge',bridge],['preparation',prep],['ingress',ingress]])need(hash(value)===p[name+'_policy_sha256']);
    const admission=ctx.admission,binding=ctx.binding,outer=ctx.source_context.binding,limit=imagePolicy.limits;
    need(exact(admission,['policy','schema','expected','compiler_manifest'])&&same(admission.policy,ingress)
        &&prep.schema_sha256===hash(ctx.preparation_schema)&&ingress.schema_sha256===hash(admission.schema));
    validate(expected,wireSchema.$defs.stager_expected,wireSchema,'image_process_context');
    validate(admission.expected,admission.schema.$defs.expected,admission.schema,'image_process_context');
    const manifest=admission.compiler_manifest,ae=admission.expected;
    need(hash(manifest)===expected.manifest_sha256&&manifest.mapping_root===layout.mapping_root
        &&Object.keys(expected).every(k=>k==='manifest_sha256'||manifest[k]===expected[k]));
    need(binding.policy_sha256===hash(imagePolicy)&&binding.compiler_version===expected.compiler_version&&binding.compiler_source_sha256===expected.compiler_source_sha256
        &&ctx.source_context.expected_sha256===hash(outer));
    for(const key of ['atlas_snapshot_id','atlas_sha256','compiler_version','compiler_source_sha256'])need(outer[key]===binding[key]);
    need(outer.invocation_id===binding.producer_run_id&&outer.invocation_id.length<=bridge.max_request_id_chars);
    for(const [key,value] of [['bridge_policy_sha256',hash(bridge)],['handoff_policy_sha256',hash(source)],['image_policy_sha256',hash(imagePolicy)],
        ['parser_virtual_path',bridge.virtual_file],['compiler_virtual_path',source.compiler_virtual_path],['atlas_relative_path',source.atlas_relative_path],
        ['auxiliary_virtual_path',source.auxiliary_virtual_path],['auxiliary_atlas_path',source.auxiliary_atlas_path],['config_virtual_path',source.config_virtual_path],['config_atlas_path',source.config_atlas_path]])need(outer[key]===value);
    need(outer.config_context_sha256===outer.auxiliary_context_sha256&&same(binding.source_sha256,{[source.compiler_virtual_path]:outer.source_sha256}));
    need(same(ctx.entries.map(row=>row.kind),p.input_kinds));
    const entries=[],records=[];let auxTotal=0,sourceTotal=0;
    try {
        for(const row of ctx.entries){
            const primary=row.kind==='source',prefix=primary?'source':row.kind;
            const path=source[primary?'compiler_virtual_path':row.kind+'_virtual_path'];
            const cap=primary?Math.min(bridge.max_source_bytes,limit.source_file_bytes):Math.min(row.kind==='config'?source.max_config_bytes:source.max_auxiliary_bytes,limit.auxiliary_file_bytes);
            need(row.path===path&&row.role===(primary?'source':'auxiliary')&&Number.isSafeInteger(row.bytes)&&row.bytes>=0&&row.bytes<=cap
                &&row.bytes===outer[prefix+'_bytes']&&row.raw_sha256===outer[primary?'source_sha256':prefix+'_raw_sha256']
                &&(primary||row.host_sha256===outer[prefix+'_host_sha256'])
                &&row.utf8Base64.length===Math.ceil(row.bytes/3)*4&&/^[A-Za-z0-9+/]*={0,2}$/.test(row.utf8Base64));
            const raw=Buffer.from(row.utf8Base64,'base64');
            need(raw.length===row.bytes&&raw.toString('base64')===row.utf8Base64&&digest(raw)===row.raw_sha256);
            entries.push({path:row.path,role:row.role,raw});
            records.push({path:row.path,role:row.role,size_bytes:row.bytes,raw_sha256:row.raw_sha256,parser_sha256:row.raw_sha256,host_sha256:row.host_sha256});
            if(primary)sourceTotal+=row.bytes;else auxTotal+=row.bytes;
        }
        need(auxTotal<=Math.min(source.max_auxiliary_bytes,limit.auxiliary_bytes)&&sourceTotal<=limit.source_bytes);
        records.sort((a,b)=>a.path<b.path?-1:a.path>b.path?1:0);
        const preparation={context_sha256:expected.context_sha256,inventory_sha256:expected.inventory_sha256,manifest_sha256:expected.manifest_sha256,
            preparation_policy_sha256:hash(prep),preparation_schema_sha256:hash(ctx.preparation_schema),image_policy_sha256:hash(imagePolicy)};
        need(same(ae,{binding_sha256:hash(binding),source_inputs_sha256:hash(records),ingress_policy_sha256:hash(ingress),
            ingress_schema_sha256:hash(admission.schema),stream_session_id:expected.session_id,preparation}));
        const refs={composition_policy_sha256:hash(p),composition_schema_sha256:hash(schema),source_context_sha256:ctx.source_context.expected_sha256};
        refs.process_context_sha256=hash({version:p.version,...refs,stream_expected:expected,layout_sha256:hash(layout),
            preparation_schema_sha256:hash(ctx.preparation_schema),ingress_expected:ae,binding_sha256:hash(binding)});
        need(same(refs,bootstrap.process_refs));
        // Raw input buffers now belong to this private consumer; release wire aliases.
        ctx.entries=null;
        return {entries,ctx,refs,p,imagePolicy,manifest};
    } catch(error){entries.length=0;throw error;}
}

function createImageProcess(bootstrap,{deadline}) {
    let held=context(bootstrap),prepared=null,instance=null,gate=null;
    const check=()=>need(performance.now()<deadline);
    const clear=()=>{instance?.dispose();release(prepared);gate?.dispose();if(held)held.entries.length=0;instance=prepared=gate=held=null;};
    try{
        check();gate=createGate(bootstrap.expected,held.manifest,bootstrap.profiles,bootstrap.schema,held.ctx.preparation_schema,{deadline});
        const publicGate=gate;
        return Object.freeze({gate:publicGate,refs:held.refs,
            complete(){
                try{
                    check();prepared=gate.prepare();
                    let configCalls=0,preprocessCalls=0,programCalls=0;
                    const tsDouble={version:held.ctx.binding.compiler_version,
                        createProgram(){programCalls++;throw Error('synthetic Program forbidden');},
                        parseConfigFileTextToJson(path,text){
                            configCalls++;try{return {config:JSON.parse(text)};}
                            catch(error){if(error instanceof SyntaxError)return {error:true};throw error;}
                        },
                        preProcessFile(){preprocessCalls++;return {typeReferenceDirectives:[],importedFiles:[],referencedFiles:[]};}};
                    instance=image.createPreparedCompilerExperiment(tsDouble,{entries:held.entries,namespace_complete:true},held.ctx.binding,held.imagePolicy,
                        prepared,held.ctx.admission,{deadline});
                    const summary=instance.summary;
                    instance.dispose();instance=null;
                    const disposed=prepared.snapshot(),raw=gate.snapshot();
                    need(disposed.state==='DISPOSED'&&disposed.retained_text_records===0&&disposed.handoff_registered===false
                        &&raw.raw_stager_state==='DISPOSED'&&raw.retained_text_records===0&&configCalls===1&&preprocessCalls===2&&programCalls===0);
                    const result={image_summary:summary,files_completed:held.manifest.files.length,raw_bytes_staged:held.manifest.files.reduce((sum,row)=>sum+row.bytes,0),
                        ...held.refs,compiler_basis:held.p.compiler_basis,source_basis:held.p.source_basis,receiver_state:'DISPOSED',prepared_state:disposed.state,
                        owned_buffer_bytes:0,retained_text_records:0,program_calls:programCalls,declaration_text_pins_verified:true,
                        image_constructor_verified_in_model:true,image_dispose_returned:true};
                    check();return result;
                } finally{clear();}
            },dispose:clear});
    }catch(error){clear();throw error;}
}

module.exports={createImageProcess,canonical};

'use strict';
// Disabled reviewed first-party fixture only. No filesystem or module resolver.
const {compileFunction}=require('node:vm'),{performance}=require('node:perf_hooks'),{types}=require('node:util');
const crypto=require('node:crypto'),path=require('node:path').posix;
const entry=require('./compiler_entry_owner.cjs');
const image=require('./ts_input_image_experiment.cjs');
const declaration=require('./compiler_declaration_text.cjs');
const text=require('./compiler_text_window.cjs');
const { _canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate}=require('./compiler_stream_stager.cjs');
const PROFILE='private_loaded_fixture_image',tokens=new WeakMap(),evaluating=new WeakMap(),failures=new WeakMap();
const freeze=Object.freeze,descriptors=Object.getOwnPropertyDescriptors,descriptor=Object.getOwnPropertyDescriptor,apply=Reflect.apply;
const hash=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
const sha=v=>crypto.createHash('sha256').update(v).digest('hex');
const bytes=v=>Buffer.byteLength(canonical(v),'utf8'),NODE=process.version;
class LoadedFixtureRefusal extends Error{constructor(code){super(code);this.code=code;}}
const need=(v,c)=>{if(!v)throw new LoadedFixtureRefusal(c);};
const sum=(...v)=>{const n=v.reduce((a,b)=>a+b,0);need(v.every(x=>Number.isSafeInteger(x)&&x>=0)&&Number.isSafeInteger(n),'fixture_budget');return n;};
function data(v,code){
    need(v&&typeof v==='object'&&!Array.isArray(v)&&!types.isProxy(v),code);
    need(Object.getPrototypeOf(v)===Object.prototype||Object.getPrototypeOf(v)===null,code);
    const d=descriptors(v);need(Reflect.ownKeys(d).every(k=>typeof k==='string'&&Object.hasOwn(d[k],'value')),code);
    return Object.fromEntries(Object.keys(d).map(k=>[k,d[k].value]));
}
function copied(v,limit,code){return text.copy(v,code,limit);}
function checked(v,name,s){validate(v,s.$defs[name],s,'fixture_'+name);}
function outputUpper(n,s){
    if(n.$ref)return outputUpper(s.$defs[n.$ref.slice('#/$defs/'.length)],s);
    if(Object.hasOwn(n,'const'))return bytes(n.const);
    if(n.enum)return Math.max(...n.enum.map(bytes));
    const kinds=Array.isArray(n.type)?n.type:[n.type];
    return Math.max(...kinds.map(k=>{
        if(k==='null')return 4;if(k==='boolean')return 5;if(k==='integer')return 16;
        if(k==='string'){need(n.pattern==='^[a-f0-9]{64}$','fixture_output_schema');return 66;}
        need(k==='object'&&n.additionalProperties===false&&n.required.length===Object.keys(n.properties).length,'fixture_output_schema');
        return 2+Math.max(0,n.required.length-1)+Object.entries(n.properties).reduce((sum,[name,value])=>sum+bytes(name)+1+outputUpper(value,s),0);
    }));
}
function off(d){return Object.fromEntries(d.denied_flags.map(k=>[k,false]));}
function record(error,c){
    if(error&&(typeof error==='object'||typeof error==='function'))failures.set(error,{...c});
}
function failureObservation(error){const x=failures.get(error);return x?{...x}:null;}
function revoke(token,state='DISPOSED'){
    const r=tokens.get(token);if(!r)return false;
    r.api=r.compiled=r.module=r.exports=null;r.state=state;tokens.delete(token);return true;
}
function policy(expectedInput,profilesInput,schemasInput){
    const initial=data(profilesInput,'fixture_policy'),first=data(initial[PROFILE],'fixture_policy');
    const limit=data(first.limits,'fixture_policy');
    need(Number.isSafeInteger(limit.context_bytes)&&limit.context_bytes>0&&limit.context_bytes<=65536,'fixture_budget');
    const held=copied({expected:expectedInput,profiles:profilesInput,schemas:schemasInput},limit.context_bytes,'fixture_context');
    const p=held.profiles,d=p[PROFILE],s=held.schemas.fixture,e=held.expected;
    need(Object.keys(held.schemas).sort().join(',')==='entry,fixture','fixture_context');
    checked(d,'policy',s);checked(e,'expected',s);
    const owner=p[d.entry_profile],capture=p[d.capture_profile],img=p[d.image_profile],ingress=p[d.ingress_profile],prep=p[d.preparation_profile];
    need(Object.keys(p).sort().join(',')===[PROFILE,d.entry_profile,d.capture_profile,d.image_profile,d.ingress_profile,d.preparation_profile].sort().join(','),'fixture_context');
    need(d.version==='v1-private-loaded-fixture-image'&&d.model==='reviewed_fixture_api_constructor_only_v1'
        &&d.evaluation_scope==='fixed_reviewed_first_party_fixture_only'&&d.transform==='identity_no_leading_bom_hashbang_v1'
        &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>d[k]===false),'fixture_policy');
    need(d.schema_sha256===hash(s)&&d.entry_policy_sha256===hash(owner)&&d.entry_schema_sha256===hash(held.schemas.entry)
        &&owner.schema_sha256===d.entry_schema_sha256&&d.capture_policy_sha256===hash(capture)
        &&d.image_policy_sha256===hash(img)&&d.ingress_policy_sha256===hash(ingress)&&d.preparation_policy_sha256===hash(prep),'fixture_identity');
    const L=d.limits;
    need(Object.values(L).every(n=>Number.isSafeInteger(n)&&n>0)&&L.metadata_bytes<=img.limits.metadata_bytes
        &&L.image_bytes<=img.limits.image_bytes&&L.operation_ms<=img.limits.capture_ms
        &&L.max_entry_bytes<=capture.max_module_bytes&&L.output_bytes<=L.context_bytes,'fixture_budget');
    need(outputUpper(s.$defs.result,s)<=L.output_bytes,'fixture_output_budget');
    need(e.policy_sha256===hash(d)&&e.schema_sha256===hash(s)&&e.node_version===NODE&&NODE===d.node_version
        &&e.compile_input_sha256===d.fixture_source_sha256&&e.compile_input_sha256===e.entry.text_sha256
        &&e.entry.entry_sha256===e.compile_input_sha256&&e.entry.policy_sha256===d.entry_policy_sha256
        &&e.entry.schema_sha256===d.entry_schema_sha256&&e.api_shape_sha256===hash(d.api_keys),'fixture_identity');
    return {held,p,d,s,e,L,owner,capture,img,ingress,prep};
}
function evaluateFixtureCompilerEntry(owner,expectedInput,profilesInput,schemasInput,optionsInput){
    need(owner!==null&&(typeof owner==='object'||typeof owner==='function'),'fixture_owner');
    const prior=evaluating.get(owner);
    if(prior){prior.poisoned=true;entry._disposeCompilerEntry(owner);throw new LoadedFixtureRefusal('fixture_reentry');}
    let ctx,api=null,compiled=null,moduleRecord=null,exportsRecord=null,token=null,observe=null,settled=false;
    const c={stage:'evaluation',compilation_attempts:0,compilations:0,invocation_attempts:0,invocations:0,dependency_requests:0,cleanup_unknown:false};
    const control={poisoned:false};evaluating.set(owner,control);
    try{
        const options=data(optionsInput,'fixture_options');
        need(Object.keys(options).every(k=>k==='now'||k==='deadline'),'fixture_options');
        const now=options.now||(()=>performance.now()),deadline=options.deadline;
        need(typeof now==='function','fixture_deadline');
        let last=now(),started=last;
        need(Number.isFinite(started)&&Number.isFinite(deadline)&&started<deadline,'fixture_deadline');
        const check=()=>{const t=now();need(!control.poisoned&&Number.isFinite(t)&&t>=last&&t<deadline,'fixture_deadline');last=t;};
        ctx=policy(expectedInput,profilesInput,schemasInput);
        const {d,e,s,L}=ctx;
        need(deadline<=started+L.operation_ms,'fixture_deadline');check();
        let snapshot=null,entryBytes=0,known=0,metadata=0;
        entry._borrowCompilerEntryText(owner,e.entry,row=>{
            check();
            const method=descriptor(owner,'snapshot');
            need(Object.isFrozen(owner)&&method&&Object.hasOwn(method,'value')&&typeof method.value==='function'&&!method.writable&&!method.configurable,'fixture_owner');
            observe=method.value;snapshot=apply(observe,owner,[]);
            validate(snapshot,ctx.held.schemas.entry.$defs.snapshot,ctx.held.schemas.entry,'fixture_owner');
            need(snapshot.state==='BORROWING'&&snapshot.entry_owner_registered&&snapshot.entry_input_verified
                &&hash(snapshot)===e.owner_snapshot_sha256,'fixture_identity');
            need(typeof row.text==='string'&&!row.text.startsWith('\ufeff')&&!row.text.startsWith('#!')
                &&row.raw_sha256===e.entry.entry_sha256&&row.text_sha256===e.compile_input_sha256
                &&sha(row.text)===d.fixture_source_sha256,'fixture_text');
            entryBytes=Buffer.byteLength(row.text,'utf8');
            need(entryBytes===row.size_bytes&&entryBytes<=L.max_entry_bytes,'fixture_budget');
            metadata=sum(3*bytes(ctx.held),bytes(snapshot),bytes({parameters:d.parameters,options:d.compile_options,paths:d.virtual_paths}));
            known=sum(snapshot.known_owner_input_bytes,entryBytes,metadata,2*L.output_bytes);
            need(metadata<=L.metadata_bytes&&known<=L.image_bytes,'fixture_budget');check();
            const requireNothing=freeze(function(){c.dependency_requests++;control.poisoned=true;throw new LoadedFixtureRefusal('fixture_dependency');});
            moduleRecord=Object.create(null);exportsRecord=Object.create(null);moduleRecord.exports=exportsRecord;
            c.stage='compile';c.compilation_attempts++;
            compiled=compileFunction(row.text,d.parameters,d.compile_options);c.compilations++;check();
            c.stage='invoke';c.invocation_attempts++;
            const returned=apply(compiled,exportsRecord,[exportsRecord,requireNothing,moduleRecord,d.virtual_paths.filename,d.virtual_paths.dirname]);
            c.invocations++;check();need(returned===undefined&&c.dependency_requests===0,'fixture_exports');
            const property=descriptor(moduleRecord,'exports');
            need(property&&Object.hasOwn(property,'value'),'fixture_exports');
            api=data(property.value,'fixture_exports');
            need(Object.keys(api).sort().join(',')===d.api_keys.slice().sort().join(',')&&api.version===d.fixture_version,'fixture_exports');
            for(const key of d.api_keys.filter(k=>k!=='version')){
                const fn=api[key];
                need(typeof fn==='function'&&!types.isProxy(fn)&&!types.isAsyncFunction(fn)&&!types.isGeneratorFunction(fn),'fixture_exports');
            }
            check();
        });
        check();
        const after=apply(observe,owner,[]);
        need(after.state==='DISPOSED'&&!after.entry_owner_registered&&after.retained_entry_bytes===0&&after.open_file_handles===0,'fixture_owner');
        const row={api,compiled,module:moduleRecord,exports:exportsRecord,ctx,state:'READY',control,check,now,deadline,started,
            evaluation_metadata_bytes:metadata,evaluation_known_inputs:known,entry_bytes:entryBytes,counters:{...c}};
        const publicSnapshot=()=>({state:row.state,registered:tokens.has(token),expected_sha256:hash(row.ctx.e),
            entry_bytes:row.state==='READY'||row.state==='BORROWING'?row.entry_bytes:0,callable_exposed:false});
        token=freeze({snapshot:publicSnapshot,dispose(){return revoke(token);},
            toJSON(){throw new TypeError('loaded_fixture_token_not_serializable');}});
        tokens.set(token,row);check();need(tokens.has(token)&&row.state==='READY','fixture_owner');
        settled=true;return token;
    }catch(error){record(error,c);throw error;}
    finally{
        entry._disposeCompilerEntry(owner);evaluating.delete(owner);
        if(!settled&&token)revoke(token,'ABORTED');
        api=compiled=moduleRecord=exportsRecord=observe=null;
    }
}
function captureInputs(value,L){
    const top=data(value,'fixture_inputs');need(Object.keys(top).sort().join(',')==='entries,namespace_complete'&&top.namespace_complete===true,'fixture_inputs');
    need(Array.isArray(top.entries)&&!types.isProxy(top.entries),'fixture_inputs');
    const list=descriptors(top.entries),count=list.length?.value;
    need(Number.isSafeInteger(count)&&count===3&&Reflect.ownKeys(list).length===count+1,'fixture_inputs');
    const entries=[];let total=0;
    for(let i=0;i<count;i++){
        need(list[i]&&Object.hasOwn(list[i],'value'),'fixture_inputs');
        const r=data(list[i].value,'fixture_inputs');
        need(Object.keys(r).sort().join(',')==='path,raw,role'&&typeof r.path==='string'&&typeof r.role==='string'
            &&!types.isProxy(r.raw)&&Buffer.isBuffer(r.raw),'fixture_inputs');
        total=sum(total,r.raw.length);need(total<=L.image_bytes,'fixture_budget');
        entries.push({path:r.path,role:r.role,raw:Buffer.from(r.raw)});
    }
    return {entries,namespace_complete:true,total};
}
// Size an inventory-shaped metadata bound, not a second admission decision.
function priceImage(inputs,ctx,check){
    const root=ctx.admission.policy.compiler_virtual_root;
    const rows=inputs.entries.map(r=>({path:r.path,size_bytes:r.raw.length}));
    for(const r of ctx.admission.compiler_manifest.files)rows.push({path:root+'/'+r.name,size_bytes:r.bytes});
    const dirs=new Map([['/',{files:[],directories:[]}]]);
    let directoryCharge=0;
    function directory(p){
        check();
        if(dirs.has(p))return;
        directoryCharge=sum(directoryCharge,bytes({path:p,files:[],directories:[]}));
        need(dirs.size<ctx.policy.limits.directories&&directoryCharge<=ctx.policy.limits.metadata_bytes,'fixture_joint_budget');
        dirs.set(p,{files:[],directories:[]});
        const parent=path.dirname(p);if(parent!==p){directory(parent);dirs.get(parent).directories.push(path.basename(p));}
    }
    let metadata=0,content=0;
    for(const r of rows){
        need(typeof r.path==='string'&&Number.isSafeInteger(r.size_bytes)&&r.size_bytes>=0,'fixture_inputs');
        directory(path.dirname(r.path));dirs.get(path.dirname(r.path)).files.push(path.basename(r.path));
        metadata+=bytes({path:r.path,role:'auxiliary',raw_sha256:'f'.repeat(64),parser_sha256:'f'.repeat(64),host_sha256:'f'.repeat(64),size_bytes:r.size_bytes});
        // Auxiliary is the longest current role; constructor still validates actual roles.
        content=sum(content,r.size_bytes);
    }
    for(const [p,v]of dirs)metadata+=bytes({path:p,files:v.files,directories:v.directories});
    metadata+=bytes(ctx.binding)+bytes(ctx.policy)+bytes({binding:ctx.binding,image_policy:ctx.policy,admission:ctx.admission});
    return {image_metadata_upper:metadata,image_content_bytes:content};
}
function constructPreparedImageFromFixtureApi(token,expectedInput,contextInput,inputsInput,declarationOwner){
    const row=tokens.get(token);
    if(!row){declaration._disposePreparedCompilerTextSet(declarationOwner);throw new LoadedFixtureRefusal('fixture_token');}
    if(row.state!=='READY'){row.control.poisoned=true;revoke(token,'ABORTED');declaration._disposePreparedCompilerTextSet(declarationOwner);throw new LoadedFixtureRefusal('fixture_reentry');}
    row.state='BORROWING';
    const check=()=>{need(tokens.get(token)===row&&row.state==='BORROWING','fixture_token');row.check();need(tokens.get(token)===row&&row.state==='BORROWING','fixture_token');};
    let instance=null,inputs=null,held=null,result=null,primary=null,clean=true,settled=false;
    const c={stage:'bridge',constructor_attempts:0,constructor_returns:0,config_calls:0,preprocess_calls:0,program_calls:0,cleanup_unknown:false};
    try{
        check();
        held=copied({expected:expectedInput,context:contextInput},row.ctx.L.context_bytes,'fixture_bridge_context');
        const {expected:e,context:ctx}=held,{d,L,s}=row.ctx;
        checked(e,'bridge_expected',s);checked(ctx.source_context,'source_context',s);
        need(Object.keys(ctx).sort().join(',')==='admission,binding,policy,source_context','fixture_bridge_context');
        need(e.loaded_expected_sha256===hash(row.ctx.e)&&e.image_context_sha256===row.ctx.e.image_context_sha256
            &&e.image_context_sha256===hash(ctx)&&e.source_context_sha256===hash(ctx.source_context)
            &&e.source_context_sha256===row.ctx.e.source_context_sha256,'fixture_image_binding');
        need(hash(ctx.policy)===d.image_policy_sha256&&hash(ctx.admission.policy)===d.ingress_policy_sha256
            &&ctx.binding.compiler_version===row.api.version&&ctx.binding.compiler_source_sha256===row.ctx.e.compile_input_sha256,'fixture_image_binding');
        const outer=ctx.source_context,b=ctx.binding;
        need(outer.invocation_id===b.producer_run_id&&outer.atlas_snapshot_id===b.atlas_snapshot_id&&outer.atlas_sha256===b.atlas_sha256
            &&outer.compiler_source_sha256===b.compiler_source_sha256&&outer.compiler_version===b.compiler_version
            &&outer.source_inputs_sha256===ctx.admission.expected.source_inputs_sha256,'fixture_source_binding');
        inputs=captureInputs(inputsInput,L);
        const price=priceImage(inputs,ctx,check),meta=sum(row.evaluation_metadata_bytes,4*bytes(held),price.image_metadata_upper);
        const known=sum(row.evaluation_known_inputs,4*bytes(held),3*inputs.total,2*price.image_content_bytes,price.image_metadata_upper,2*L.output_bytes);
        need(meta<=L.metadata_bytes&&known<=L.image_bytes,'fixture_joint_budget');check();
        const call=(key,args)=>{
            check();c[key==='parseConfigFileTextToJson'?'config_calls':'preprocess_calls']++;
            need(c.config_calls<=1&&c.preprocess_calls<=2,'fixture_consumer_scope');
            const value=apply(row.api[key],undefined,args);check();return value;
        };
        const api=freeze({version:row.api.version,
            parseConfigFileTextToJson(...args){return call('parseConfigFileTextToJson',args);},
            preProcessFile(...args){return call('preProcessFile',args);},
            createProgram(){c.program_calls++;row.control.poisoned=true;throw new LoadedFixtureRefusal('fixture_program_forbidden');}});
        c.stage='constructor';c.constructor_attempts++;
        instance=image.createPreparedCompilerExperiment(api,{entries:inputs.entries,namespace_complete:true},ctx.binding,ctx.policy,
            declarationOwner,ctx.admission,{deadline:row.deadline,now:row.now});
        c.constructor_returns++;check();
        const summary=copied(instance.summary,L.output_bytes,'fixture_output');
        need(c.config_calls===1&&c.preprocess_calls===2&&c.program_calls===0
            &&summary.admission_stage_known_metadata_bytes<=price.image_metadata_upper
            &&summary.content_bytes===price.image_content_bytes,'fixture_joint_budget');
        instance.dispose();instance=null;check();
        result={version:d.version,status:d.result_status,loaded_expected_sha256:hash(row.ctx.e),image_context_sha256:e.image_context_sha256,
            source_context_sha256:e.source_context_sha256,entry_text_sha256:row.ctx.e.compile_input_sha256,
            image_summary:summary,compilations:row.counters.compilations,invocations:row.counters.invocations,
            constructor_calls:c.constructor_returns,config_calls:c.config_calls,preprocess_calls:c.preprocess_calls,program_calls:c.program_calls,
            joint_metadata_reserved_bytes:meta,known_joint_inputs_reserved_bytes:known,image_metadata_upper:price.image_metadata_upper,
            evaluated_fixture_api_used:true,entry_owner_disposed:true,declaration_owner_disposed:true,image_disposed:true,
            loaded_token_disposed:true,callable_exposed:false,whole_image_within_limit:null,compiled_heap_bytes:null,runtime_heap_bytes:null,peak_rss_bytes:null,flags:off(d)};
        checked(result,'result',s);need(bytes(result)<=L.output_bytes,'fixture_output');check();
        settled=true;
    }catch(error){primary=error;}
    finally{
        try{instance?.dispose();}catch(error){clean=false;if(!primary)primary=error;}
        try{declaration._disposePreparedCompilerTextSet(declarationOwner);}catch(error){clean=false;if(!primary)primary=error;}
        revoke(token,settled&&clean&&!primary?'DISPOSED':'ABORTED');
        if(inputs)inputs.entries.length=0;inputs=held=instance=null;
    }
    if(primary){c.cleanup_unknown=!clean;record(primary,c);throw primary;}
    need(clean&&settled,'fixture_cleanup');
    return freeze(result);
}
module.exports={evaluateFixtureCompilerEntry,constructPreparedImageFromFixtureApi,
    loadedFixtureFailureObservation:failureObservation,LoadedFixtureRefusal};

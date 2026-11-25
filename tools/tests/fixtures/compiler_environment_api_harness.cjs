'use strict';
// Fresh owned child; synthetic entry paths only, never installed TypeScript.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),vm=require('node:vm'),Module=require('node:module'),{types}=require('node:util');
const request=JSON.parse(fs.readFileSync(0,'utf8'));
const profileId=request.profile_id||'private_compiler_environment_api';
const ownerSource=require('../../core/compiler_entry_owner.cjs');
const canonical=require('../../core/compiler_stream_stager.cjs')._canonicalCompilerStreamMetadata;
const native=Object.fromEntries(['realpathSync','statSync','openSync','fstatSync','readSync','closeSync','writeSync'].map(k=>[k,fs[k]]));
const realCreate=vm.createContext,realCompile=vm.compileFunction,realApply=Reflect.apply,realFreeze=Object.freeze,realDispose=ownerSource._disposeCompilerEntry;
const get=Object.getOwnPropertyDescriptor,hash=value=>crypto.createHash('sha256').update(value).digest('hex');
const original=new RangeError('controlled environment failure'),cleanupOriginal=new URIError('controlled cleanup failure'),out={},fds=new Map();
let phase='setup',clock=0,once=false,contexts=0,compileAttempts=0,compilations=0,entryCalls=0,bridgeCalls=0,getterCalls=0,apiCalls=0;
let owner,environment,foreignEnvironment,token,knownBridgeGetter,knownGetter,knownApi,knownGraph,createdContext,opens=0,closes=0,entryOpens=0,forbidden=0,moduleLoads=0,badInputs=0,cleanupFaulted=false;
const realOwnKeys=Reflect.ownKeys;let inventoryKeys=0,inventoryDescriptors=0;
const sandbox=path.resolve(request.sandbox),entryPath=path.resolve(request.entry_path),deadline=1000;
function evaluate(...args){if(profileId==='private_compiler_export_inventory'&&!request.entrypoint)return environment.evaluateSyntheticCompilerInventoryEnvironment(...args);
    if(request.entrypoint==='accessor')return environment.evaluateSyntheticCompilerAccessorEnvironment(...args);
    return profileId==='private_compiler_accessor_bridge'&&request.entrypoint!=='data'
    ?environment.evaluateSyntheticCompilerAccessorEnvironment(...args):environment.evaluateSyntheticCompilerEnvironment(...args);}
function apiExpected(){const e=request.env_expected;return {entry:e.entry,environment_policy_sha256:e.environment_policy_sha256,
    environment_schema_sha256:e.environment_schema_sha256,api_selection_sha256:e.api_selection_sha256,
    operation:request.profiles[profileId].api_selection.operation,consumer_sha256:e.consumer_sha256,
    ...(['private_compiler_accessor_bridge','private_compiler_export_inventory'].includes(profileId)?{bridge_selection_sha256:e.bridge_selection_sha256,bridge_descriptor_sha256:e.bridge_descriptor_sha256}:{}),
    ...(profileId==='private_compiler_export_inventory'?Object.fromEntries(['inventory_selection_sha256','inventory_manifest_sha256','inventory_names_sha256','inventory_rows_sha256'].map(k=>[k,e[k]])): {})};}
function failure(value){
    return {error:value instanceof environment.CompilerEnvironmentRefusal||value instanceof ownerSource.CompilerEntryRefusal
        ||(foreignEnvironment&&value instanceof foreignEnvironment.CompilerEnvironmentRefusal)?value.code:
        value===original?'injected_original':value&&typeof value==='object'?value.name:String(value),
        stage:environment._compilerEnvironmentFailureStage(value),original_identity:value===original,cleanup_identity:value===cleanupOriginal,
        aggregate_primary_original:value instanceof AggregateError&&value.errors.includes(original),
        aggregate_cleanup_original:value instanceof AggregateError&&value.errors.includes(cleanupOriginal)};
}
const now=()=>{
    if(phase==='inventory_keys_settled'&&request.fault==='inventory_clock_error')throw original;
    if(phase==='inventory_keys_settled'&&request.fault==='dispose_entry_at_inventory')owner.dispose();
    if(phase==='inventory_keys_settled'&&request.fault==='inventory_reentry'&&!once){once=true;try{environment.evaluateSyntheticCompilerAccessorEnvironment(owner,{},request.profiles,{}, {now,deadline});}catch{}}
    if(phase==='evaluation'&&request.fault==='initial_deadline')return deadline;
    if(phase==='evaluation'&&request.fault==='initial_clock_error')throw original;
    if(phase==='evaluation'&&request.fault==='nonfinite_clock')return NaN;
    if(phase==='cleanup'&&request.fault==='final_deadline')return deadline;
    if(phase==='cleanup'&&request.fault==='final_clock_error')throw original;
    if(phase==='cleanup'&&request.fault==='final_reentry'&&!once){once=true;try{evaluate(owner,request.env_expected,request.profiles,request.schemas,{now,deadline});}catch{}}
    if(phase==='api_ready'&&!once&&['wrong_operation','dispose_api','consume_before_consumer','foreign_api','api_clone','api_inherited','api_proxy','api_fake'].includes(request.fault)){
        once=true;
        if(request.fault==='dispose_api')environment._disposeCompilerEnvironmentApi(token);
        else{
            const expected=apiExpected();if(request.fault==='wrong_operation')expected.operation='wrong';
            let chosen=token;
            if(request.fault==='api_clone')chosen={...token};
            if(request.fault==='api_inherited')chosen=Object.create(token);
            if(request.fault==='api_proxy')chosen=new Proxy(token,{get(){badInputs++;throw original;}});
            if(request.fault==='api_fake')chosen={kind:'private_synthetic_compiler_api',get snapshot(){badInputs++;throw original;}};
            try{out.external_consume=(request.fault==='foreign_api'?foreignEnvironment:environment)._consumeCompilerEnvironmentApi(chosen,expected);}
            catch(error){out.external_failure=failure(error);}
        }
    }
    return clock;
};
vm.createContext=function(globals,options){
    phase='context';out.context_options_sha256=hash(canonical(options));
    if(request.fault==='context_error')throw original;
    if(request.fault==='bad_context')return {};
    createdContext=realCreate(globals,options);contexts++;
    out.denied_globals_held=request.profiles[profileId].denied_globals.every(k=>{const d=get(createdContext,k);return d&&Object.hasOwn(d,'value')&&d.value===undefined&&!d.writable&&!d.configurable;});
    if(request.fault==='after_context_deadline')clock=deadline;
    return createdContext;
};
vm.compileFunction=function(text,params,options){
    phase='compile';compileAttempts++;
    out.compile_input_sha256=hash(text);out.parameters_sha256=hash(canonical(params));
    const {parsingContext,...metadata}=options;out.compile_options_sha256=hash(canonical(metadata));out.same_context=parsingContext===createdContext&&vm.isContext(parsingContext);
    if(request.fault==='compile_error')throw original;
    if(request.fault==='mutate_expected'){request.env_expected.result_sha256='0'.repeat(64);request.profiles[profileId].denied_globals=[];}
    const compiled=realCompile(text,params,options);compilations++;
    if(request.fault==='after_compile_deadline')clock=deadline;
    return function(...args){
        phase='entry';entryCalls++;
        if(request.fault==='entry_error')throw original;
        const value=realApply(compiled,this,args);
        const bridge=get(args[2],'exports'),graph=bridge&&Object.hasOwn(bridge,'value')?bridge.value:null;
        knownBridgeGetter=bridge?.get;
        if(graph&&typeof graph==='object'&&!types.isProxy(graph))knownGetter=get(graph,'readFixtureValue')?.get;
        phase='entry_settled';if(request.fault==='after_entry_deadline')clock=deadline;
        return value;
    };
};
Reflect.apply=function(fn,receiver,args){
    if(fn===knownBridgeGetter){
        phase='bridge';bridgeCalls++;
        if(request.fault==='bridge_error')throw original;
        if(request.fault==='dispose_entry_at_bridge')owner.dispose();
        const value=realApply(fn,receiver,args);
        knownGraph=value;
        if(value&&typeof value==='object'&&!types.isProxy(value))knownGetter=get(value,'readFixtureValue')?.get;
        if(request.fault==='bridge_cross_mode_reentry'){try{environment.evaluateSyntheticCompilerEnvironment(owner,{},request.profiles,{}, {now,deadline});}catch{}}
        phase='bridge_settled';if(request.fault==='after_bridge_deadline')clock=deadline;
        return value;
    }
    if(fn===knownGetter){
        phase='getter';getterCalls++;
        if(request.fault==='getter_error')throw original;
        if(request.fault==='dispose_entry_at_getter')owner.dispose();
        const value=realApply(fn,receiver,args);knownApi=value;phase='getter_settled';
        if(request.fault==='after_getter_deadline')clock=deadline;
        return value;
    }
    if(fn===knownApi){
        phase='api';apiCalls++;
        if(request.fault==='api_error'||request.fault==='primary_and_cleanup')throw original;
        if(request.fault==='dispose_entry_at_api')owner.dispose();
        if(request.fault==='api_reentry'){try{environment._consumeCompilerEnvironmentApi(token,apiExpected());}catch{}}
        const value=realApply(fn,receiver,args);phase='projection';
        if(request.fault==='after_api_deadline')clock=deadline;
        if(request.fault==='backward_clock')clock=-1;
        return value;
    }
    return realApply(fn,receiver,args);
};
Object.freeze=function(value){
    if(value?.kind==='private_synthetic_compiler_api'){token=value;phase='api_ready';}
    if(['SYNTHETIC_ENVIRONMENT_API_OBSERVED','SYNTHETIC_ACCESSOR_BRIDGE_API_OBSERVED','SYNTHETIC_EXPORT_INVENTORY_API_OBSERVED'].includes(value?.status)){
        out.unpublished_candidate_seen=true;phase='publication';if(request.fault==='publication_error')throw original;
    }
    return realFreeze(value);
};
ownerSource._disposeCompilerEntry=function(value){
    const result=realDispose(value);phase='cleanup';
    if(!cleanupFaulted&&['cleanup_error','primary_and_cleanup'].includes(request.fault)){cleanupFaulted=true;throw cleanupOriginal;}
    return result;
};
Reflect.ownKeys=function(value){
    if(profileId==='private_compiler_export_inventory'&&value===knownGraph){
        phase='inventory_keys';inventoryKeys++;
        if(request.fault==='inventory_keys_error')throw original;
        const keys=realOwnKeys(value);phase='inventory_keys_settled';
        if(request.fault==='after_inventory_keys_deadline')clock=deadline;
        return keys;
    }
    return realOwnKeys(value);
};
Object.getOwnPropertyDescriptor=function(value,key){
    if(profileId==='private_compiler_export_inventory'&&value===knownGraph&&phase.startsWith('inventory')){
        inventoryDescriptors++;
        if(request.fault==='inventory_descriptor_error')throw original;
    }
    return get(value,key);
};
environment=require('../../core/compiler_environment_api.cjs');
if(request.fault==='foreign_api'){
    const filename=require.resolve('../../core/compiler_environment_api.cjs'),other=new Module(filename);
    other.filename=filename;other.paths=module.paths;other._compile(fs.readFileSync(filename,'utf8'),filename);foreignEnvironment=other.exports;
}
let factory=ownerSource.createCompilerEntryOwner;
if(request.foreign_entry){
    const filename=require.resolve('../../core/compiler_entry_owner.cjs'),other=new Module(filename);
    other.filename=filename;other.paths=module.paths;other._compile(fs.readFileSync(filename,'utf8'),filename);factory=other.exports.createCompilerEntryOwner;
}
function scoped(value){const full=path.resolve(String(value)),rel=path.relative(sandbox,full);
    if(rel==='..'||rel.startsWith('..'+path.sep)||path.isAbsolute(rel)){forbidden++;throw Error('outside fixture');}return full;}
fs.realpathSync=value=>{scoped(value);const result=native.realpathSync(value);scoped(result);return result;};
fs.statSync=value=>{scoped(value);return native.statSync(value);};
fs.openSync=(value,...args)=>{const p=scoped(value),fd=native.openSync(value,...args);fds.set(fd,p);opens++;if(p===entryPath)entryOpens++;return fd;};
fs.fstatSync=fd=>native.fstatSync(fd);fs.readSync=(...args)=>native.readSync(...args);
fs.closeSync=fd=>{native.closeSync(fd);fds.delete(fd);closes++;};
Module._load=()=>{moduleLoads++;throw Error('late module load forbidden');};
for(const key of ['readFileSync','readFile','open','readdirSync','readdir','writeFileSync','writeFile','appendFileSync'])fs[key]=()=>{forbidden++;throw Error('unowned IO');};
globalThis.__ENV_MARKER=41;
try{
    owner=factory(request.root,request.owner_expected,request.profiles,request.owner_schema,{now,deadline});out.ready=owner.snapshot();
    if(request.fault==='mutate_file'){
        const fd=native.openSync(entryPath,'w');try{const data=Buffer.alloc(request.owner_expected.entry_bytes,120);native.writeSync(fd,data,0,data.length,null);}finally{native.closeSync(fd);}
    }
    let inputOwner=owner,expected=request.env_expected;
    if(request.owner_kind==='clone')inputOwner={...owner};
    if(request.owner_kind==='inherited')inputOwner=Object.create(owner);
    if(request.owner_kind==='proxy')inputOwner=new Proxy(owner,{get(){badInputs++;throw original;}});
    if(request.owner_kind==='fake')inputOwner={get snapshot(){badInputs++;throw original;}};
    if(request.owner_kind==='disposed')owner.dispose();
    if(request.expected_kind==='accessor')Object.defineProperty(expected,'result_sha256',{enumerable:true,get(){badInputs++;throw original;}});
    if(request.expected_kind==='proxy')expected=new Proxy(expected,{ownKeys(){badInputs++;throw original;}});
    if(request.expected_kind==='symbol')expected[Symbol('unexpected')]='0'.repeat(64);
    if(request.expected_kind==='nested_accessor')Object.defineProperty(expected.entry,'entry_sha256',{enumerable:true,get(){badInputs++;throw original;}});
    if(request.profile_kind==='accessor')Object.defineProperty(request.profiles,profileId,{get(){badInputs++;throw original;}});
    if(request.profile_kind==='proxy')request.profiles=new Proxy(request.profiles,{getOwnPropertyDescriptor(){badInputs++;throw original;}});
    phase='evaluation';
    try{out.result=evaluate(inputOwner,expected,request.profiles,request.schemas,{now,deadline});}
    catch(error){out.failure=failure(error);}
    out.after=owner.snapshot();
    if(request.repeat){phase='repeat';try{evaluate(owner,request.env_expected,request.profiles,request.schemas,{now,deadline});}catch(error){out.repeat_failure=failure(error);}}
}catch(error){out.constructor_failure=failure(error);}
finally{
    if(owner)out.before_fixture_cleanup=owner.snapshot();owner?.dispose();
    for(const fd of [...fds.keys()]){native.closeSync(fd);fds.delete(fd);out.emergency_fixture_close=true;}
}
process.stdout.write(JSON.stringify({...out,node_version:process.version,contexts,compile_attempts:compileAttempts,compilations,entry_calls:entryCalls,
    bridge_calls:bridgeCalls,getter_calls:getterCalls,api_calls:apiCalls,inventory_keys:inventoryKeys,inventory_descriptors:inventoryDescriptors,bad_inputs:badInputs,host_marker:globalThis.__ENV_MARKER,
    forbidden_accesses:forbidden,forbidden_module_loads:moduleLoads,opens,closes,entry_opens:entryOpens,open_fds:fds.size})+'\n');

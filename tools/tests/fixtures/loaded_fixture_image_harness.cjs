'use strict';
// Owned finite fixtures only; no installed compiler or target repository.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto'),Module=require('node:module');
const request=JSON.parse(fs.readFileSync(0,'utf8'));
const ownerApi=require('../../core/compiler_entry_owner.cjs');
const image=require('../../core/ts_input_image_experiment.cjs'),decl=require('../../core/compiler_declaration_text.cjs');
const native=Object.fromEntries(['realpathSync','statSync','openSync','fstatSync','readSync','closeSync','writeSync'].map(k=>[k,fs[k]]));
const nativeCompile=vm.compileFunction,nativeFreeze=Object.freeze,load=Module._load;
let compileAttempts=0,compiled=0,invokeAttempts=0,invoked=0,config=0,preprocess=0,program=0,constructors=0,disposed=0,forbidden=0,publicCalls=0;
let phase='setup',clock=0,entryOwner,loaded,declarations,gate,bridge,once=false;
const original=new RangeError('fixture image injected failure'),fds=new Map(),entryPath=path.resolve(request.entry_path);
const out={},deadline=1000;
const now=()=>{
    if(request.fault==='clock_backwards'&&phase==='bridge')return -1;
    if(request.fault==='deadline'&&phase==='bridge')return deadline;
    if(request.fault==='late_deadline'&&phase==='disposed')return deadline;
    if(request.fault==='clock_throw'&&phase==='bridge')throw original;
    if(request.fault==='dispose_late'&&phase==='disposed'&&!once){once=true;loaded.dispose();}
    if(request.fault==='reenter_late'&&phase==='disposed'&&!once){
        once=true;try{bridge.constructPreparedImageFromFixtureApi(loaded,request.bridge_expected,request.context,inputs,declarations);}catch{}
    }
    return clock;
};
vm.compileFunction=function(text,parameters,options){
    compileAttempts++;out.compile_input_sha256=crypto.createHash('sha256').update(text).digest('hex');
    if(request.fault==='compile_throw')throw original;
    const fn=nativeCompile(text,parameters,options);compiled++;
    return function(...args){
        invokeAttempts++;if(request.fault==='invoke_throw')throw original;
        if(request.fault==='evaluate_reentry'){try{bridge.evaluateFixtureCompilerEntry(entryOwner,request.expected,request.profiles,request.schemas,{now,deadline});}catch{}}
        const value=Reflect.apply(fn,this,args);invoked++;
        if(request.fault==='dependency_caught'){try{args[1]('not-permitted');}catch{}}
        if(request.fault==='exports_proxy')args[2].exports=new Proxy(args[2].exports,{ownKeys(){publicCalls++;throw original;}});
        if(request.fault==='exports_accessor'){const e={...args[2].exports};Object.defineProperty(e,'version',{enumerable:true,get(){publicCalls++;throw original;}});args[2].exports=e;}
        if(request.fault==='exports_extra')args[2].exports={...args[2].exports,extra:true};
        if(request.fault==='exports_async')args[2].exports={...args[2].exports,preProcessFile:async()=>({})};
        if(request.fault==='exports_generator')args[2].exports={...args[2].exports,preProcessFile:function*(){}};
        if(request.fault==='wrong_version')args[2].exports={...args[2].exports,version:'wrong'};
        if(request.fault==='wrapper_return')return 1;
        return value;
    };
};
const createImage=image.createPreparedCompilerExperiment;
image.createPreparedCompilerExperiment=function(ts,...args){
    constructors++;phase='constructor';
    const api={version:ts.version,createProgram(...a){program++;return ts.createProgram(...a);},
        parseConfigFileTextToJson(...a){config++;if(request.fault==='config_throw')throw original;return ts.parseConfigFileTextToJson(...a);},
        preProcessFile(...a){preprocess++;if(request.fault==='preprocess_throw')throw original;return ts.preProcessFile(...a);}};
    if(request.fault==='force_program'){try{api.createProgram();}catch{}}
    const result=createImage.call(this,api,...args),release=result.dispose;
    return {...result,dispose(){disposed++;release();phase='disposed';if(request.fault==='cleanup_throw')throw original;}};
};
Object.freeze=function(value){
    if(value?.status==='EVALUATED_FIXTURE_API_IMAGE_CONSTRUCTED_NOT_COMPILER_ADMISSION'&&request.fault==='result_allocation_throw')throw original;
    return nativeFreeze(value);
};
bridge=require('../../core/loaded_fixture_image.cjs');
let evaluator=bridge;
if(request.token_kind==='foreign'){
    const filename=require.resolve('../../core/loaded_fixture_image.cjs'),m=new Module(filename);
    m.filename=filename;m.paths=module.paths;m._compile(fs.readFileSync(filename,'utf8'),filename);evaluator=m.exports;
}
function scoped(value){const p=path.resolve(String(value)),rel=path.relative(request.sandbox,p);
    if(rel==='..'||rel.startsWith('..'+path.sep)||path.isAbsolute(rel)){forbidden++;throw Error('outside fixture');}return p;}
fs.realpathSync=value=>{scoped(value);const r=native.realpathSync(value);scoped(r);return r;};
fs.statSync=value=>{scoped(value);return native.statSync(value);};
fs.openSync=(value,...args)=>{const p=scoped(value),fd=native.openSync(value,...args);fds.set(fd,p);if(p===entryPath)out.entry_opens=(out.entry_opens||0)+1;return fd;};
fs.fstatSync=fd=>native.fstatSync(fd);fs.readSync=(...a)=>native.readSync(...a);
fs.closeSync=fd=>{native.closeSync(fd);fds.delete(fd);};
Module._load=function(name,...args){if(name==='./compiler_declaration_text.cjs')return load.call(this,name,...args);forbidden++;throw Error('late dependency');};
for(const key of ['readFileSync','readFile','open','readdirSync','readdir','writeFileSync','writeFile'])fs[key]=()=>{forbidden++;throw Error('unowned IO');};
const material=request.material;
const inputs={entries:material.entries.map(r=>({path:r.path,role:r.role,raw:Buffer.from(r.raw_base64,'base64')})),namespace_complete:true};
try{
    gate=decl.createCompilerDeclarationTextHandoffPreparation(material.expected,material.manifest,material.profiles,material.transport_schema,material.text_schema,{now,deadline});
    for(const f of material.frames){const raw=Buffer.from(f,'base64'),ack=gate.feed(raw);if(JSON.parse(raw).kind!=='commit')gate.release_credit(ack);}
    gate.end_input();declarations=gate.prepare();
    entryOwner=ownerApi.createCompilerEntryOwner(request.root,request.owner_expected,request.owner_profiles,request.owner_schema,{now,deadline});
    if(request.fault==='replace_file'){
        const fd=native.openSync(entryPath,'w');try{const raw=Buffer.alloc(request.owner_expected.entry_bytes,120);native.writeSync(fd,raw,0,raw.length,null);}finally{native.closeSync(fd);}
    }
    let expected=request.expected;
    if(request.expected_kind==='proxy')expected=new Proxy(expected,{ownKeys(){publicCalls++;throw original;}});
    if(request.expected_kind==='accessor')Object.defineProperty(expected,'compile_input_sha256',{enumerable:true,get(){publicCalls++;throw original;}});
    phase='evaluate';loaded=evaluator.evaluateFixtureCompilerEntry(entryOwner,expected,request.profiles,request.schemas,{now,deadline});
    out.ready=loaded.snapshot();
    try{JSON.stringify(loaded);out.serialization_refused=false;}catch{out.serialization_refused=true;}
    let token=loaded;
    if(request.token_kind==='clone')token={...loaded};
    if(request.token_kind==='proxy')token=new Proxy(loaded,{get(){publicCalls++;throw original;}});
    if(request.token_kind==='fake')token={get snapshot(){publicCalls++;throw original;},dispose(){publicCalls++;}};
    if(request.token_kind==='disposed')loaded.dispose();
    if(request.fault==='metadata_mutation_after_evaluation'){request.expected.image_context_sha256='0'.repeat(64);request.profiles.private_loaded_fixture_image.limits.image_bytes=1;}
    if(request.input_fault==='proxy')inputs.entries[0]=new Proxy(inputs.entries[0],{ownKeys(){publicCalls++;throw original;}});
    phase='bridge';
    out.result=bridge.constructPreparedImageFromFixtureApi(token,request.bridge_expected,request.context,inputs,declarations);
    if(request.repeat){try{bridge.constructPreparedImageFromFixtureApi(loaded,request.bridge_expected,request.context,inputs,declarations);}catch(e){out.repeat=e.code||e.name;}}
}catch(error){out.error=error===original?'injected_original':error.code||error.name;out.original_identity=error===original;out.failure=bridge.loadedFixtureFailureObservation(error)||evaluator.loadedFixtureFailureObservation(error);}
finally{
    out.entry_snapshot=entryOwner?.snapshot();out.token_snapshot=loaded?.snapshot();out.declaration_snapshot=declarations?.snapshot();
    loaded?.dispose();entryOwner?.dispose();declarations?.dispose();gate?.dispose();
    for(const fd of fds.keys()){native.closeSync(fd);out.emergency_close=true;}fds.clear();
}
process.stdout.write(JSON.stringify({...out,exports:Object.keys(bridge).sort(),node_version:process.version,compile_attempts:compileAttempts,compilations:compiled,invocation_attempts:invokeAttempts,invocations:invoked,
    constructor_calls:constructors,config_calls:config,preprocess_calls:preprocess,program_calls:program,image_disposals:disposed,
    forbidden_accesses:forbidden,public_calls:publicCalls,open_fds:fds.size,actual_typescript_loads:0})+'\n');

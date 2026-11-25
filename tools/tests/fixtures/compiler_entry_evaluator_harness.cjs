'use strict';
// Owned short-lived child for reviewed tiny first-party fixture bodies only.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),vm=require('node:vm'),Module=require('node:module');
const request=JSON.parse(fs.readFileSync(0,'utf8'));
const ownerSource=require('../../core/compiler_entry_owner.cjs');
const native=Object.fromEntries(['realpathSync','statSync','openSync','fstatSync','readSync','closeSync','writeSync'].map(k=>[k,fs[k]]));
const compile=vm.compileFunction,freeze=Object.freeze,getDescriptors=Object.getOwnPropertyDescriptors,createHash=crypto.createHash;
const original=new RangeError('controlled scalar evaluator failure'),runtimeSyntax=new SyntaxError('controlled runtime syntax failure');
const hash=value=>createHash('sha256').update(value).digest('hex');
const canonical=require('../../core/compiler_stream_stager.cjs')._canonicalCompilerStreamMetadata;
let phase='setup',clock=0,compileAttempts=0,compilations=0,invocations=0,forbidden=0,moduleLoads=0,opens=0,closes=0,entryOpens=0;
let projectionCalls=0,badOwnerCalls=0,owner,evaluator,once=false,capturedSummary=null;
const deadline=1000,sandbox=path.resolve(request.sandbox),entryPath=path.resolve(request.entry_path),fds=new Map();
const now=()=>{
    if(phase==='evaluation'&&request.fault==='initial_deadline')return deadline;
    if(phase==='evaluation'&&request.fault==='nonfinite_clock')return NaN;
    if(phase==='evaluation'&&request.fault==='initial_clock_error')throw original;
    if(phase==='publication'&&request.fault==='final_deadline')return deadline;
    if(phase==='publication'&&request.fault==='final_clock_error')throw original;
    if(phase==='publication'&&request.fault==='final_reentry'&&!once){once=true;try{evaluator.evaluateSyntheticCompilerEntry(owner,request.eval_expected,request.profiles,request.schemas,{now,deadline});}catch{}}
    return clock;
};
vm.compileFunction=function(text,params,options){
    phase='compilation';compileAttempts++;
    out.compile_input_sha256=hash(text);out.parameters_sha256=hash(canonical(params));out.options_sha256=hash(canonical(options));
    if(request.fault==='compile_error')throw original;
    if(request.fault==='mutate_expected'){
        request.eval_expected.result_sha256='0'.repeat(64);request.profiles.private_compiler_entry_evaluator.parameters[0]='changed';
    }
    const fn=compile(text,params,options);compilations++;
    if(request.fault==='after_compile_deadline')clock=deadline;
    return function(...args){
        phase='invocation';invocations++;
        if(request.fault==='invoke_error')throw original;
        if(request.fault==='runtime_syntax_error')throw runtimeSyntax;
        const value=Reflect.apply(fn,this,args);phase='projection';
        if(request.fault==='after_invoke_deadline')clock=deadline;
        if(request.fault==='backward_clock')clock=-1;
        return value;
    };
};
Object.getOwnPropertyDescriptors=function(value){
    if(phase==='projection'&&value&&Object.hasOwn(value,'value')){
        projectionCalls++;
        if(request.fault==='projection_error')throw original;
        if(request.fault==='after_projection_deadline')clock=deadline;
    }
    return getDescriptors(value);
};
Object.freeze=function(value){
    if(value&&value.status==='SYNTHETIC_ENTRY_EVALUATED_SCALAR_ONLY'){
        phase='publication';capturedSummary=value;if(request.fault==='publication_error')throw original;
    }
    return freeze(value);
};
evaluator=require('../../core/compiler_entry_evaluator.cjs');
let factory=ownerSource.createCompilerEntryOwner;
if(request.foreign){
    const filename=require.resolve('../../core/compiler_entry_owner.cjs'),other=new Module(filename);
    other.filename=filename;other.paths=module.paths;other._compile(fs.readFileSync(filename,'utf8'),filename);factory=other.exports.createCompilerEntryOwner;
}
function scoped(value){
    const full=path.resolve(String(value)),rel=path.relative(sandbox,full);
    if(rel==='..'||rel.startsWith('..'+path.sep)||path.isAbsolute(rel)){forbidden++;throw Error('outside owned fixture');}
    return full;
}
fs.realpathSync=value=>{scoped(value);const real=native.realpathSync(value);scoped(real);return real;};
fs.statSync=value=>{scoped(value);return native.statSync(value);};
fs.openSync=(value,...args)=>{const p=scoped(value),fd=native.openSync(value,...args);fds.set(fd,p);opens++;if(p===entryPath)entryOpens++;return fd;};
fs.fstatSync=fd=>native.fstatSync(fd);
fs.readSync=(...args)=>native.readSync(...args);
fs.closeSync=fd=>{native.closeSync(fd);fds.delete(fd);closes++;};
Module._load=()=>{moduleLoads++;throw Error('late module load forbidden');};
for(const key of ['readFileSync','readFile','open','readdirSync','readdir','writeFileSync','writeFile','appendFileSync'])fs[key]=()=>{forbidden++;throw Error('unowned IO forbidden');};
const out={};
function error(value){return {error:value instanceof evaluator.CompilerEvaluatorRefusal||value instanceof ownerSource.CompilerEntryRefusal?value.code:
    value===original?'injected_original':value.name,original_identity:value===original||value===runtimeSyntax};}
try{
    owner=factory(request.root,request.owner_expected,request.profiles,request.owner_schema,{now,deadline});
    out.ready=owner.snapshot();
    if(request.fault==='mutate_file'){
        const fd=native.openSync(entryPath,'w');try{const data=Buffer.alloc(request.owner_expected.entry_bytes,120);native.writeSync(fd,data,0,data.length,null);}finally{native.closeSync(fd);}
    }
    globalThis.__disposeEntry=()=>owner.dispose();
    globalThis.__reenterEvaluator=()=>{try{evaluator.evaluateSyntheticCompilerEntry(owner,request.eval_expected,request.profiles,request.schemas,{now,deadline});}catch{}};
    globalThis.__throwOriginal=()=>{throw original;};
    globalThis.__getterCalls=0;globalThis.__syntheticMarker=0;
    let token=owner,expected=request.eval_expected;
    if(request.owner_kind==='clone')token={...owner};
    if(request.owner_kind==='inherited')token=Object.create(owner);
    if(request.owner_kind==='proxy')token=new Proxy(owner,{get(){badOwnerCalls++;throw original;}});
    if(request.owner_kind==='fake')token={get snapshot(){badOwnerCalls++;throw original;},dispose(){badOwnerCalls++;}};
    if(request.owner_kind==='disposed')owner.dispose();
    if(request.expected_kind==='accessor')Object.defineProperty(expected,'result_sha256',{enumerable:true,get(){badOwnerCalls++;throw original;}});
    if(request.expected_kind==='symbol')expected[Symbol('extra')]='0'.repeat(64);
    if(request.expected_kind==='proxy')expected=new Proxy(expected,{ownKeys(){badOwnerCalls++;throw original;}});
    phase='evaluation';
    try{out.result=evaluator.evaluateSyntheticCompilerEntry(token,expected,request.profiles,request.schemas,{now,deadline});}
    catch(value){out.failure=error(value);}
    out.after=owner.snapshot();
    if(request.repeat){phase='repeat';try{evaluator.evaluateSyntheticCompilerEntry(owner,request.eval_expected,request.profiles,request.schemas,{now,deadline});}catch(value){out.repeat_failure=error(value);}}
}catch(value){out.constructor_failure=error(value);}
finally{
    if(owner)out.before_fixture_cleanup=owner.snapshot();
    owner?.dispose();
    for(const fd of [...fds.keys()]){native.closeSync(fd);fds.delete(fd);out.emergency_fixture_close=true;}
}
process.stdout.write(JSON.stringify({...out,node_version:process.version,compile_attempts:compileAttempts,compilations,invocations,
    projection_calls:projectionCalls,bad_owner_calls:badOwnerCalls,getter_calls:globalThis.__getterCalls||0,marker:globalThis.__syntheticMarker||0,
    forbidden_accesses:forbidden,forbidden_module_loads:moduleLoads,opens,closes,entry_opens:entryOpens,open_fds:fds.size,
    captured_unpublished_summary:!!capturedSummary&&!out.result})+'\n');

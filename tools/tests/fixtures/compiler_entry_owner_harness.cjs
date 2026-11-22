'use strict';
// First-party test adapter only. Retained entry files are never evaluated.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),Module=require('node:module');
const request=JSON.parse(fs.readFileSync(0,'utf8'));
const source=require('../../core/compiler_entry_owner.cjs');let factory=source.createCompilerEntryOwner;
if(request.foreign){
    const filename=require.resolve('../../core/compiler_entry_owner.cjs'),other=new Module(filename);
    other.filename=filename;other.paths=module.paths;other._compile(fs.readFileSync(filename,'utf8'),filename);
    factory=other.exports.createCompilerEntryOwner;
}
const {performance}=require('node:perf_hooks');
const native=Object.fromEntries(['realpathSync','statSync','openSync','fstatSync','readSync','closeSync','writeSync'].map(key=>[key,fs[key]]));
// Fixture writer owns a separate uninstrumented descriptor; it must not recurse
// through the owner's read-only open/close spies or inflate its capture counts.
function rewrite(data){
    const fd=native.openSync(entryPath,'w');
    try{let offset=0;while(offset<data.length)offset+=native.writeSync(fd,data,offset,data.length-offset,null);}
    finally{native.closeSync(fd);}
}
const freeze=Object.freeze,alloc=Buffer.alloc,Decoder=global.TextDecoder,createHash=crypto.createHash;
const original=new RangeError('controlled entry-owner failure'),secondary=new Error('controlled close failure');
const sandbox=path.resolve(request.sandbox),entryPath=path.resolve(request.entry_path),fds=new Map();
let forbidden=0,forbiddenModules=0,opens=0,closes=0,entryOpens=0,entryReads=0,readBytes=0,entryClosed=false;
let clock=0,armed=false,afterOwner=0,constructing=true,owner,captured,consumerCalls=0,badOwnerCalls=0,getterCalls=0;
let clockCalls=0,traceReentered=false;
const entryActive=()=>[...fds.values()].includes(entryPath);
const hash=value=>createHash('sha256').update(value).digest('hex');
const deadline=request.real_clock?performance.now()+1000:1000;
const now=()=>{
    clockCalls++;
    if(request.trace&&request.fault==='trace_reentry'&&!traceReentered){
        traceReentered=true;
        try{source.observeSyntheticEntryOwnerLifetime(request.root,request.trace_expected,request.profiles,
            {entry:request.schema,trace:request.trace_schema},{deadline,now});}catch{}
    }
    if(request.trace&&['trace_borrow_deadline','trace_borrow_throw','trace_borrow_reentry'].includes(request.fault)
        &&captured?.snapshot().state==='BORROWING'){
        if(request.fault==='trace_borrow_deadline')return deadline;
        if(request.fault==='trace_borrow_throw')throw original;
        if(!traceReentered){traceReentered=true;try{source._borrowCompilerEntryText(captured,request.borrow_expected,()=>null);}catch{}}
    }
    if(request.fault==='backward_clock'&&clockCalls>1)return -1;
    if(request.fault==='nonfinite_clock'&&clockCalls>1)return NaN;
    if(request.fault==='initial_deadline')return deadline;
    if(armed){afterOwner++;if(request.fault==='publication_deadline'||request.fault==='final_deadline'&&afterOwner>=2)return deadline;}
    if(request.fault==='after_entry_clock_throw'&&entryClosed)throw original;
    if(request.fault==='after_entry_deadline'&&entryClosed)return deadline;
    if(request.fault==='callback_clock_throw'&&!constructing&&consumerCalls)return (()=>{throw original;})();
    return request.real_clock?performance.now():clock;
};
function scoped(value){
    const candidate=path.resolve(String(value)),relative=path.relative(sandbox,candidate);
    if(relative==='..'||relative.startsWith('..'+path.sep)||path.isAbsolute(relative)){forbidden++;throw Error('outside synthetic fixture');}
    return candidate;
}
fs.realpathSync=value=>{
    const candidate=scoped(value);
    if(request.fault==='member_alias'&&candidate===entryPath)return path.join(path.dirname(candidate),'different.js');
    const real=native.realpathSync(value);scoped(real);return real;
};
fs.statSync=value=>{scoped(value);return native.statSync(value);};
fs.openSync=(value,...args)=>{
    const candidate=scoped(value);
    if(candidate===entryPath&&request.fault==='open_error')throw original;
    if(candidate===entryPath&&request.fault==='mutate_before_read')rewrite(Buffer.alloc(request.expected.entry_bytes,120));
    const fd=native.openSync(value,...args);fds.set(fd,candidate);opens++;if(candidate===entryPath)entryOpens++;return fd;
};
fs.fstatSync=fd=>{
    const value=native.fstatSync(fd);
    if(fds.get(fd)===entryPath&&request.fault==='nonregular')return {size:value.size,isFile:()=>false};
    if(fds.get(fd)===entryPath&&request.fault==='stat_over')return {size:request.profiles.compiler_library_inputs.max_module_bytes+1,isFile:()=>true};
    return value;
};
fs.readSync=(fd,buffer,offset,length,position)=>{
    const entry=fds.get(fd)===entryPath;
    if(entry&&['read_error','read_and_close'].includes(request.fault))throw original;
    if(entry&&request.fault==='read_errno')throw Object.assign(Error('private unavailable path'),{code:'EACCES'});
    if(entry){entryReads++;if(request.fault==='shrink'&&entryReads===1)rewrite(Buffer.alloc(0));}
    const n=native.readSync(fd,buffer,offset,entry&&['partial','mutate_during_read','grow'].includes(request.fault)?Math.min(length,2):length,position);
    readBytes+=n;
    if(entry&&entryReads===1&&request.fault==='mutate_during_read')rewrite(Buffer.alloc(request.expected.entry_bytes,120));
    if(entry&&entryReads===1&&request.fault==='grow')rewrite(Buffer.alloc(request.expected.entry_bytes+1,120));
    return n;
};
fs.closeSync=fd=>{
    const entry=fds.get(fd)===entryPath;native.closeSync(fd);fds.delete(fd);closes++;if(entry)entryClosed=true;
    if(entry&&request.fault==='close_error')throw original;
    if(entry&&request.fault==='read_and_close')throw secondary;
};
Buffer.alloc=function(...args){if(entryActive()&&request.fault==='alloc_error')throw original;return alloc.apply(Buffer,args);};
global.TextDecoder=class extends Decoder {decode(...args){if(entryClosed&&request.fault==='decode_error')throw original;return super.decode(...args);}};
crypto.createHash=function(...args){const value=createHash(...args),update=value.update;value.update=function(data,...rest){
    if(entryActive()&&request.fault==='hash_error'&&Buffer.isBuffer(data))throw original;
    return update.call(this,data,...rest);
};return value;};
Object.freeze=function(value){
    if(constructing&&value?.snapshot&&value?.dispose&&value?.toJSON){
        captured=value;armed=true;if(request.fault==='owner_allocation_error')throw original;
    }
    return freeze(value);
};
Module._load=()=>{forbiddenModules++;throw Error('entry/module evaluation forbidden');};
for(const key of ['readFileSync','readFile','open','readdirSync','readdir','writeFileSync','writeFile','appendFileSync'])fs[key]=()=>{forbidden++;throw Error('unowned fixture IO');};
let out={events:[]};
function error(value){return {error:value instanceof source.CompilerEntryRefusal?value.code:value===original?'injected_original':value.name,original_identity:value===original,
    ...(request.trace?{trace_failure:source.compilerEntryLifetimeTraceFailureObservation(value)}:{})};}
try{
    if(request.trace){
        out.trace=source.observeSyntheticEntryOwnerLifetime(request.root,request.trace_expected,request.profiles,
            {entry:request.schema,trace:request.trace_schema},{deadline,now,...request.trace_options});
    }else{
    owner=factory(request.root,request.expected,request.profiles,request.schema,{deadline,now});constructing=false;
    out.ready=owner.snapshot();
    if(request.fault==='mutate_after_capture')rewrite(Buffer.alloc(request.expected.entry_bytes,120));
    for(const step of request.steps||[{op:'borrow'}]){
        try{
            let value;
            if(step.op==='dispose')value={disposed:source._disposeCompilerEntry(owner)};
            else if(step.op==='serialize'){let refused=false;try{JSON.stringify(owner);}catch{refused=true;}value={refused};}
            else{
                let token=owner,expected={...request.borrow_expected};
                if(step.kind==='clone')token={...owner};
                if(step.kind==='proxy')token=new Proxy(owner,{get(){badOwnerCalls++;throw original;}});
                if(step.kind==='fake')token={borrow(){badOwnerCalls++;},dispose(){badOwnerCalls++;}};
                if(step.kind==='inherited')token=Object.create(owner);
                if(step.key)expected[step.key]='0'.repeat(64);
                if(step.kind==='extra')expected.extra='0'.repeat(64);
                if(step.kind==='symbol')expected[Symbol('extra')]='0'.repeat(64);
                if(step.kind==='accessor')Object.defineProperty(expected,'entry_sha256',{enumerable:true,get(){getterCalls++;return request.borrow_expected.entry_sha256;}});
                if(step.kind==='expected_reentry'||step.kind==='expected_dispose')expected=new Proxy(expected,{ownKeys(target){
                    if(step.kind==='expected_dispose')owner.dispose();
                    else try{source._borrowCompilerEntryText(owner,request.borrow_expected,()=>null);}catch{}
                    return Reflect.ownKeys(target);
                }});
                const callback=row=>{
                    consumerCalls++;
                    if(step.kind==='throw')throw original;
                    if(step.kind==='dispose_self')owner.dispose();
                    if(step.kind==='reentry')try{source._borrowCompilerEntryText(owner,request.borrow_expected,()=>null);}catch{}
                    if(step.kind==='thenable')return {then(){}};
                    if(step.kind==='then_getter')return {get then(){throw original;}};
                    if(step.kind==='then_dispose')return {get then(){owner.dispose();return undefined;}};
                    if(step.kind==='expire')clock=deadline;
                    if(step.kind==='mutate_expected')request.borrow_expected.entry_sha256='0'.repeat(64);
                    return {raw_sha256:row.raw_sha256,text_sha256:hash(row.text),size_bytes:row.size_bytes,code_units:row.text.length,
                        raw_exposed:Object.hasOwn(row,'raw'),frozen:Object.isFrozen(row)};
                };
                value=source._borrowCompilerEntryText(token,expected,step.kind==='async'?async()=>null:step.kind==='generator'?function*(){}:callback);
            }
            out.events.push({value,snapshot:owner.snapshot()});
        }catch(value){out.events.push({...error(value),snapshot:owner.snapshot()});}
    }
    }
}catch(value){out.constructor_error=error(value);}
finally{
    if(owner)out.final_snapshot=owner.snapshot();
    if(captured&&!owner)out.unpublished_snapshot=captured.snapshot();
    owner?.dispose();
    for(const fd of [...fds.keys()]){native.closeSync(fd);fds.delete(fd);out.emergency_fixture_close=true;}
}
process.stdout.write(JSON.stringify({...out,node_version:process.version,forbidden_accesses:forbidden,forbidden_module_loads:forbiddenModules,
    opens,closes,entry_opens:entryOpens,entry_reads:entryReads,read_bytes:readBytes,open_fds:fds.size,
    consumer_calls:consumerCalls,bad_owner_calls:badOwnerCalls,getter_calls:getterCalls,entry_evaluated:globalThis.__ENTRY_OWNER_MARKER__===true})+'\n');

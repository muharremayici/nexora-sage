'use strict';
// Authored temporary fixtures only. Preload first-party code before denying loaders/I/O.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const q=JSON.parse(fs.readFileSync(0,'utf8')),source=require('../../core/compiler_entry_owner.cjs');
const native=Object.fromEntries(['realpathSync','statSync','openSync','fstatSync','readSync','closeSync'].map(k=>[k,fs[k]]));
const sandbox=path.resolve(q.sandbox),entry=path.resolve(q.entry_path),fds=new Map(),alloc=Buffer.alloc;
let opens=0,closes=0,reads=0,delivered=0,forbidden=0,loads=0,entryClosed=false,getters=0,reentered=false;
const original=new RangeError('controlled synthetic identity fault');
function scoped(value){const candidate=path.resolve(value),relative=path.relative(sandbox,candidate);
    if(relative==='..'||relative.startsWith('..'+path.sep)||path.isAbsolute(relative)){forbidden++;throw Error('outside fixture');}return candidate;}
fs.realpathSync=value=>{scoped(value);const out=native.realpathSync(value);scoped(out);return q.fault==='member_alias'&&path.resolve(value)===entry?out+'.alias':out;};
fs.statSync=value=>{scoped(value);return native.statSync(value);};
fs.openSync=(value,...args)=>{const p=scoped(value);if(q.fault==='open_error'&&p===entry)throw original;
    const fd=native.openSync(value,...args);fds.set(fd,p);opens++;return fd;};
fs.fstatSync=fd=>{const s=native.fstatSync(fd);return q.fault==='nonregular'&&fds.get(fd)===entry?{size:s.size,isFile:()=>false}:s;};
fs.readSync=(fd,b,o,n,p)=>{reads++;if(['read_error','read_and_close_error'].includes(q.fault)&&fds.get(fd)===entry)throw original;
    const count=native.readSync(fd,b,o,q.fault==='partial'?Math.min(n,2):n,p);delivered+=count;return count;};
fs.closeSync=fd=>{const isEntry=fds.get(fd)===entry;native.closeSync(fd);fds.delete(fd);closes++;if(isEntry)entryClosed=true;
    if(isEntry&&['close_error','read_and_close_error'].includes(q.fault))throw original;};
Buffer.alloc=function(...args){if(q.fault==='alloc_error'&&[...fds.values()].includes(entry))throw original;return alloc.apply(Buffer,args);};
for(const k of ['readFileSync','readFile','readdirSync','readdir','open','writeFileSync','appendFileSync'])fs[k]=()=>{forbidden++;throw Error('unowned fixture access');};
Module._load=()=>{loads++;throw Error('input/module evaluation forbidden');};
if(q.fault==='getter')Object.defineProperty(q.expected,'roles',{get(){getters++;throw original;}});
if(q.fault==='proxy')q.expected=new Proxy(q.expected,{});
let clock=0;
const now=()=>{
    if(entryClosed&&q.fault==='deadline')return 1000;
    if(entryClosed&&q.fault==='clock_error')throw original;
    if(entryClosed&&q.fault==='primitive_error')throw 'controlled primitive';
    if(entryClosed&&q.fault==='reentry'&&!reentered){reentered=true;try{source.observeSyntheticCompilerEntryIdentity(q.root,q.expected,q.profiles,q.schemas,{now:()=>0,deadline:1000});}catch{}}
    return clock;
};
let result=null,error=null,failure=null,brands=[],failureCopyIsolated=null;
try{
    result=source.observeSyntheticCompilerEntryIdentity(q.root,q.expected,q.profiles,q.schemas,{now,deadline:1000});
    for(const call of [()=>source._borrowCompilerEntryText(result,{},()=>{throw Error('borrowed source');}),
        ()=>source.observeCompilerTextWindowCandidate(result,{},{},{},{}),()=>source.observeCompilerOwnedRowFragment(result,{},{},{},{})]){
        try{call();brands.push('unexpected_acceptance');}catch(e){brands.push(e.code||e.name);}
    }
}catch(e){error={code:e?.code||e?.name||typeof e,original_identity:e===original};failure=source.compilerEntryIdentityFailureObservation(e);
    if(failure){const n=failure.accounting.open_attempts;failure.accounting.open_attempts=-1;
        failure=source.compilerEntryIdentityFailureObservation(e);failureCopyIsolated=failure.accounting.open_attempts===n;}}
process.stdout.write(JSON.stringify({result,error,failure,brands,opens,closes,reads,delivered,open_fds:fds.size,forbidden,loads,getters,
    input_evaluated:globalThis.__FORBIDDEN_EXECUTION__===true,failure_copy_isolated:failureCopyIsolated,node_version:process.version}));

'use strict';
// Authored temporary fixtures; all first-party code preloaded before I/O gates.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),Module=require('node:module');
const q=JSON.parse(fs.readFileSync(0,'utf8')),source=require('../../core/compiler_entry_owner.cjs');
const native=Object.fromEntries(['realpathSync','statSync','openSync','fstatSync','readSync','closeSync','writeSync'].map(k=>[k,fs[k]]));
const createHash=crypto.createHash,sandbox=path.resolve(q.sandbox),entry=path.resolve(q.entry_path),fds=new Map();
let opens=0,closes=0,reads=0,delivered=0,forbidden=0,loads=0,uses=0,getters=0,afterUse=false,fixtureMutationWrites=0,useClocks=0,usePrototypeGets=0,usePrototypeSets=0;
const original=new RangeError('controlled fixed-use fault');
function scoped(v){const p=path.resolve(v),r=path.relative(sandbox,p);if(r==='..'||r.startsWith('..'+path.sep)||path.isAbsolute(r)){forbidden++;throw original;}return p;}
fs.realpathSync=v=>{scoped(v);const p=native.realpathSync(v);scoped(p);return p;};
fs.statSync=v=>{scoped(v);return native.statSync(v);};
fs.openSync=(v,...args)=>{const p=scoped(v);const fd=native.openSync(v,...args);fds.set(fd,p);opens++;return fd;};
fs.fstatSync=fd=>{if(!fds.has(fd))throw original;return native.fstatSync(fd);};
fs.readSync=(fd,b,o,n,p)=>{if(!fds.has(fd))throw original;reads++;const count=native.readSync(fd,b,o,q.fault==='partial'?Math.min(n,3):n,p);delivered+=count;return count;};
fs.closeSync=fd=>{native.closeSync(fd);fds.delete(fd);closes++;};
for(const k of ['readFileSync','readFile','open','readdirSync','readdir','writeFileSync','appendFileSync'])fs[k]=()=>{forbidden++;throw original;};
Module._load=()=>{loads++;throw original;};
crypto.createHash=function(...args){
    const h=createHash.apply(crypto,args),update=h.update.bind(h),digest=h.digest.bind(h);let consumer=false;
    h.update=(v,encoding)=>{
        if(encoding==='utf8'){
            consumer=true;uses++;if(fds.size!==0||opens!==5)throw original;
            if(q.fault==='throw')throw original;
            if(q.fault==='mutate'){
                const fd=native.openSync(scoped(entry),'w'),data=Buffer.from('changed after acquisition');
                try{let offset=0;while(offset<data.length){const n=native.writeSync(fd,data,offset,data.length-offset);if(n<=0)throw original;offset+=n;}fixtureMutationWrites++;}
                finally{native.closeSync(fd);}
            }
            if(q.fault==='reentry_old'||q.fault==='reentry_new'){
                try{if(q.fault==='reentry_old')source.observeSyntheticCompilerEntryIdentity(q.root,q.expected.identity_expected,q.profiles,
                    {identity:q.schemas.identity,entry:q.schemas.entry},{now:()=>0,deadline:1000});
                else source.observeSyntheticCompilerEntryUse(q.root,q.expected,q.profiles,q.schemas,{now:()=>0,deadline:1000});}catch{}
            }
            afterUse=true;
        }
        return update(v,encoding);
    };
    h.digest=(...args)=>consumer&&q.fault==='digest'?'0'.repeat(64):digest(...args);return h;
};
if(q.fault==='getter')Object.defineProperty(q.expected,'policy_sha256',{get(){getters++;throw original;}});
if(q.fault==='proxy')q.expected=new Proxy(q.expected,{});
if(q.fault==='prototype_use')Object.defineProperty(Object.prototype,'use',{configurable:true,get(){usePrototypeGets++;throw original;},set(){usePrototypeSets++;throw original;}});
// Sixth post-hash sample is the common publication check, after fixed-use validation.
const now=()=>{if(afterUse)useClocks++;if(afterUse&&q.fault==='deadline'||q.fault==='publication_deadline'&&useClocks===6)return 1000;
    if(afterUse&&q.fault==='clock_error')throw original;return 0;};
let result=null,error=null,failure=null,brands=[],copyIsolated=null;
try{
    result=q.route==='identity'?source.observeSyntheticCompilerEntryIdentity(q.root,q.expected.identity_expected,q.profiles,
        {identity:q.schemas.identity,entry:q.schemas.entry},{now,deadline:1000}):
        source.observeSyntheticCompilerEntryUse(q.root,q.expected,q.profiles,q.schemas,{now,deadline:1000,...(q.options||{})});
    for(const call of [()=>source._borrowCompilerEntryText(result,{},()=>{throw original;}),
        ()=>source.observeCompilerTextWindowCandidate(result,{},{},{},{}),()=>source.observeCompilerOwnedRowFragment(result,{},{},{},{})]){
        try{call();brands.push('unexpected_acceptance');}catch(e){brands.push(e.code||e.name);}
    }
}catch(e){
    error={code:e?.code||e?.name||typeof e,original_identity:e===original};failure=source.compilerEntryUseFailureObservation(e);
    if(failure){const n=failure.accounting.open_attempts;failure.accounting.open_attempts=-1;failure=source.compilerEntryUseFailureObservation(e);copyIsolated=failure.accounting.open_attempts===n;}
}
if(q.fault==='prototype_use')delete Object.prototype.use;
process.stdout.write(JSON.stringify({result,error,failure,brands,opens,closes,reads,delivered,uses,use_prototype_gets:usePrototypeGets,use_prototype_sets:usePrototypeSets,fixture_mutation_writes:fixtureMutationWrites,open_fds:fds.size,forbidden,loads,getters,
    input_evaluated:globalThis.__FORBIDDEN_EXECUTION__===true,failure_copy_isolated:copyIsolated,node_version:process.version}));

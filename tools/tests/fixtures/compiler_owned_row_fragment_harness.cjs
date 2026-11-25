'use strict';
// Controller-created temporary original fixtures only. Never an installed package.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),vm=require('node:vm');
const request=JSON.parse(fs.readFileSync(0,'utf8')),out={},native={},fds=new Map();
for(const k of ['realpathSync','statSync','openSync','fstatSync','readSync','closeSync','readFileSync'])native[k]=fs[k];
let forbidden=0,backend=0,loads=0,bad=0,opens=0,closes=0,entryOpens=0,readBytes=0,clock=0,phase='construction',once=false,owner,token,points=0;
const original=new RangeError('owned-row injected failure'),cleanupOriginal=new URIError('owned-row cleanup failure');
for(const k of ['compileFunction','createContext','runInContext','runInNewContext','runInThisContext','Script'])vm[k]=()=>{backend++;throw Error('input backend forbidden');};
const source=require('../../core/compiler_entry_owner.cjs'),data=require('../../core/compiler_owned_row_fragment.cjs');
const legacy=require('../../core/compiler_export_recipe.cjs'),scalar=require('../../core/compiler_entry_evaluator.cjs'),environment=require('../../core/compiler_environment_api.cjs');
let foreign;
if(request.owner_kind==='foreign'||request.fault==='foreign_capsule'){
    const file=require.resolve('../../core/compiler_entry_owner.cjs'),other=new Module(file);other.filename=file;other.paths=module.paths;
    other._compile(native.readFileSync(file,'utf8'),file);foreign=other.exports;
}
const sandbox=path.resolve(request.sandbox),entryPath=path.resolve(request.entry_path),deadline=1000;
function scoped(value){const full=path.resolve(String(value)),rel=path.relative(sandbox,full);if(rel==='..'||rel.startsWith('..'+path.sep)||path.isAbsolute(rel)){forbidden++;throw Error('outside synthetic fixture');}return full;}
fs.realpathSync=value=>{scoped(value);const result=native.realpathSync(value);scoped(result);return result;};
fs.statSync=value=>{scoped(value);return native.statSync(value);};
fs.openSync=(value,...args)=>{scoped(value);const fd=native.openSync(value,...args);fds.set(fd,path.resolve(value));opens++;if(path.resolve(value)===entryPath)entryOpens++;return fd;};
fs.fstatSync=fd=>native.fstatSync(fd);
fs.readSync=(...args)=>{const n=native.readSync(...args);readBytes+=n;return n;};
fs.closeSync=fd=>{native.closeSync(fd);fds.delete(fd);closes++;if(request.fault==='close_error'&&!once){once=true;throw cleanupOriginal;}};
for(const k of ['readFileSync','readFile','open','readdirSync','readdir','writeFileSync','writeFile','appendFileSync'])fs[k]=()=>{forbidden++;throw Error('unowned fixture IO');};
Module._load=()=>{loads++;throw Error('late module load forbidden');};
const freeze=Object.freeze,codePoint=String.prototype.codePointAt,mapGet=WeakMap.prototype.get;
function failure(e){return {code:e?.code||e?.name||String(e),stage:data.failureStage(e),original:e===original,cleanup:e===cleanupOriginal,
    primary_original:e instanceof AggregateError&&e.errors.includes(original),cleanup_original:e instanceof AggregateError&&e.errors.includes(cleanupOriginal)};}
function invoke(chosen=owner){return source.observeCompilerOwnedRowFragment(chosen,request.observe_expected||request.expected,request.profiles,request.schemas,{now,deadline:request.extended_deadline?2000:deadline});}
const now=()=>{
    if(phase==='construction'&&request.fault==='expired_construction')clock=deadline;
    if(phase==='observation'&&['initial_error','primary_and_cleanup'].includes(request.fault))throw original;
    if(phase==='observation'&&request.fault==='expired_observation')clock=deadline;
    if(phase==='observation'&&request.fault==='backwards_clock')return -1;
    if(phase==='capsule'&&!once){
        if(request.fault==='consume_dispose'){once=true;source._disposeCompilerOwnedRowFragmentCapsule(token);}
        if(request.fault==='consume_reentry'){once=true;try{source._consumeCompilerOwnedRowFragmentCapsule(token,request.expected);}catch{}}
        if(request.fault==='wrong_capsule'||request.fault==='foreign_capsule'){once=true;const target=request.fault==='foreign_capsule'?foreign:source;
            const dispose=request.fault==='foreign_capsule'?'_disposeCompilerOwnedRowFragmentCapsule':'_disposeCompilerTextWindowCapsule';
            const consume=request.fault==='foreign_capsule'?'_consumeCompilerOwnedRowFragmentCapsule':'_consumeCompilerTextWindowCapsule';
            out.wrong_dispose=target[dispose](token);try{target[consume](token,{get entry(){bad++;throw original;}});}catch(e){out.wrong_failure=failure(e);}}
    }
    if(phase==='publication'){
        if(request.fault==='publication_error')throw original;
        if(request.fault==='publication_deadline')clock=deadline;
        if(request.fault==='extended_owner_deadline')clock=1500;
        if(request.fault==='publication_reentry'&&!once){once=true;try{invoke();}catch{}}
    }
    return clock;
};
Object.freeze=value=>{
    if(Object.getOwnPropertyDescriptor(value||{},'kind')?.value==='private_owned_row_fragment_capsule'){token=value;phase='capsule';}
    if(/^(OWNED_LOCAL_ROW_FRAGMENT|INCOMPLETE_OWNED_LOCAL_ROW_FRAGMENT)/.test(Object.getOwnPropertyDescriptor(value||{},'status')?.value||''))phase='publication';
    return freeze(value);
};
WeakMap.prototype.get=function(key){const row=mapGet.call(this,key);
    if(request.fault==='primary_and_cleanup'&&row?.operation?.descriptor?.model==='declared_entry_exact_view_owned_local_rows_v1'&&!row.injected){
        row.injected=true;const dispose=row.dispose;row.dispose=()=>{dispose();throw cleanupOriginal;};
    }return row;
};
String.prototype.codePointAt=function(at){points++;phase='scan';
    if(!once&&request.fault?.startsWith('scan_')){once=true;
        if(request.fault==='scan_error')throw original;if(request.fault==='scan_deadline')clock=deadline;
        if(request.fault==='scan_dispose')owner.dispose();if(request.fault==='scan_reentry'){try{invoke();}catch{}}
    }
    if(request.fault==='primary_and_cleanup')throw original;
    return codePoint.call(this,at);
};
globalThis.__OWNED_ROW_MARKER=41;
try{
    if(request.expected_kind==='proxy')request.expected=new Proxy(request.expected,{ownKeys(){bad++;throw original;}});
    if(request.expected_kind==='accessor')Object.defineProperty(request.expected,'base_request',{enumerable:true,get(){bad++;throw original;}});
    if(request.expected_kind==='cycle')request.expected.cycle=request.expected;
    if(request.expected_kind==='symbol')request.expected[Symbol('unexpected')]=0;
    if(request.owner_kind==='synthetic')owner=source.createCompilerEntryOwner(request.root,request.old_expected,request.profiles,request.old_schema,{now,deadline});
    else if(request.owner_kind==='text')owner=source.createCompilerTextWindowOwner(request.root,request.expected.base_request,request.profiles,{entry:request.schemas.entry,text_window:request.schemas.text_window},{now,deadline});
    else owner=(request.owner_kind==='foreign'?foreign:source).createCompilerOwnedRowFragmentOwner(request.root,request.expected,request.profiles,request.schemas,{now,deadline});
    out.ready=owner.snapshot();
    if(request.wrong_consumers){
        try{source._borrowCompilerEntryText(owner,request.expected.base_request.entry,()=>{bad++;});}catch(e){out.old_borrow=failure(e);}
        out.old_dispose=source._disposeCompilerEntry(owner);
        try{source.observeCompilerTextWindowCandidate(owner,request.expected.base_request,request.profiles,{entry:request.schemas.entry,text_window:request.schemas.text_window},{now,deadline});}catch(e){out.old_text=failure(e);}
        for(const [label,call] of [['scalar',()=>scalar.evaluateSyntheticCompilerEntry(owner,{},request.profiles,{}, {now,deadline})],
            ['environment',()=>environment.evaluateSyntheticCompilerEnvironment(owner,{},request.profiles,{}, {now,deadline})],
            ['helper',()=>legacy.observeSyntheticCompilerHelperWindow(owner,{},request.profiles,{}, {now,deadline})]]){
            try{call();}catch(e){out[label]=failure(e);}
        }
    }
    let chosen=owner;
    if(request.owner_kind==='clone')chosen={...owner};if(request.owner_kind==='inherited')chosen=Object.create(owner);
    if(request.owner_kind==='proxy')chosen=new Proxy(owner,{get(){bad++;throw original;}});
    if(request.owner_kind==='fake')chosen={get snapshot(){bad++;throw original;}};
    if(request.owner_kind==='disposed')owner.dispose();
    phase='observation';try{out.result=invoke(chosen);}catch(e){out.failure=failure(e);}
    out.after=owner.snapshot();
    if(request.repeat){phase='repeat';try{invoke();}catch(e){out.repeat_failure=failure(e);}}
    if(request.worker_shape)out.worker_exports=Object.keys(data);
}catch(e){out.constructor_failure=failure(e);}
finally{if(owner){out.before_cleanup=owner.snapshot();owner.dispose();out.after_cleanup=owner.snapshot();}for(const fd of fds.keys()){native.closeSync(fd);out.emergency_close=true;}fds.clear();}
process.stdout.write(JSON.stringify({...out,opens,closes,entry_opens:entryOpens,read_bytes:readBytes,open_fds:fds.size,forbidden_accesses:forbidden,
    forbidden_module_loads:loads,backend_invocations:backend,bad_inputs:bad,codepoint_observations:points,execution_marker:globalThis.__OWNED_ROW_MARKER})+'\n');

'use strict';
// Only controller-created temporary synthetic inputs. No installed package reads.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),vm=require('node:vm'),crypto=require('node:crypto');
const request=JSON.parse(fs.readFileSync(0,'utf8')),out={},fds=new Map(),native={};
for(const k of ['realpathSync','statSync','openSync','fstatSync','readSync','closeSync','readFileSync'])native[k]=fs[k];
let backend=0,bad=0,opens=0,closes=0,entryOpens=0,forbidden=0,moduleLoads=0,phase='construction',clock=0,once=false,token,owner;
let wholeEncodes=0,wholeHashes=0,viewCopies=0,viewEncodes=0,viewHashes=0,codepoints=0,capsuleTicks=0;
const original=new RangeError('text-window injected fault'),cleanupOriginal=new URIError('text cleanup fault');
for(const key of ['compileFunction','createContext','runInContext','runInNewContext','runInThisContext','Script'])vm[key]=()=>{backend++;throw Error('input backend forbidden');};
const source=require('../../core/compiler_entry_owner.cjs'),kernel=require('../../core/compiler_text_window.cjs');
const legacy=require('../../core/compiler_export_recipe.cjs'),scalar=require('../../core/compiler_entry_evaluator.cjs'),environment=require('../../core/compiler_environment_api.cjs');
let foreign;
if(request.owner_kind==='foreign'||request.fault==='foreign_capsule'){
    const file=require.resolve('../../core/compiler_entry_owner.cjs'),other=new Module(file);other.filename=file;other.paths=module.paths;
    other._compile(native.readFileSync(file,'utf8'),file);foreign=other.exports;
}
const sandbox=path.resolve(request.sandbox),entryPath=path.resolve(request.entry_path),deadline=1000;
const freeze=Object.freeze,codepoint=String.prototype.codePointAt,slice=String.prototype.slice,bufferFrom=Buffer.from,createHash=crypto.createHash;
function failure(e){return {code:e?.code||e?.name||String(e),stage:kernel.failureStage(e),original:e===original,cleanup:e===cleanupOriginal,
    primary_original:e instanceof AggregateError&&e.errors.includes(original),cleanup_original:e instanceof AggregateError&&e.errors.includes(cleanupOriginal)};}
function invoke(chosen=owner){return source.observeCompilerTextWindowCandidate(chosen,request.expected,request.profiles,request.schemas,{now,deadline:request.extended_deadline?2000:deadline});}
function scoped(value){const full=path.resolve(String(value)),relative=path.relative(sandbox,full);if(relative==='..'||relative.startsWith('..'+path.sep)||path.isAbsolute(relative)){forbidden++;throw Error('outside synthetic fixture');}return full;}
fs.realpathSync=value=>{scoped(value);const result=native.realpathSync(value);scoped(result);return result;};
fs.statSync=value=>{scoped(value);return native.statSync(value);};
fs.openSync=(value,...args)=>{scoped(value);const fd=native.openSync(value,...args);fds.set(fd,path.resolve(value));opens++;if(path.resolve(value)===entryPath)entryOpens++;return fd;};
fs.fstatSync=fd=>native.fstatSync(fd);fs.readSync=(...args)=>native.readSync(...args);
fs.closeSync=fd=>{native.closeSync(fd);fds.delete(fd);closes++;if(request.fault==='close_error'&&!once){once=true;throw cleanupOriginal;}};
for(const key of ['readFileSync','readFile','open','readdirSync','readdir','writeFileSync','writeFile','appendFileSync'])fs[key]=()=>{forbidden++;throw Error('unowned fixture IO');};
Module._load=()=>{moduleLoads++;throw Error('late module load forbidden');};
const now=()=>{
    if(phase==='capsule'){
        capsuleTicks++;
        if(request.fault==='consume_dispose'&&capsuleTicks===3)source._disposeCompilerTextWindowCapsule(token);
        if(request.fault==='consume_reentry'&&capsuleTicks===3){try{source._consumeCompilerTextWindowCapsule(token,request.expected);}catch{}}
        if(request.fault==='wrong_capsule'&&!once){once=true;out.wrong_dispose=legacy._disposeCompilerHelperWindowCapsule(token);
            try{legacy._consumeCompilerHelperWindowCapsule(token,{get entry(){bad++;throw original;}});}catch(e){out.wrong_failure=failure(e);}}
        if(request.fault==='foreign_capsule'&&!once){once=true;out.wrong_dispose=foreign._disposeCompilerTextWindowCapsule(token);
            try{foreign._consumeCompilerTextWindowCapsule(token,{get entry(){bad++;throw original;}});}catch(e){out.wrong_failure=failure(e);}}
    }
    if(phase==='publication'){
        if(request.fault==='publication_deadline')clock=deadline;
        if(request.fault==='publication_extended_deadline')clock=1500;
        if(request.fault==='publication_error')throw original;
        if(request.fault==='publication_reentry'&&!once){once=true;try{invoke();}catch{}}
    }
    if(phase==='observation'&&request.fault==='initial_deadline')clock=deadline;
    if(phase==='observation'&&request.fault==='initial_clock_error')throw original;
    return clock;
};
Object.freeze=function(value){
    if(Object.getOwnPropertyDescriptor(value||{},'kind')?.value==='private_declared_text_window_capsule'){token=value;phase='capsule';}
    if(/^(TEXT_WINDOW_CANDIDATE|INCOMPLETE_TEXT_WINDOW_CANDIDATE)/.test(Object.getOwnPropertyDescriptor(value||{},'status')?.value||''))phase='publication';
    return freeze(value);
};
Buffer.from=function(value,...rest){
    if(phase==='observation'&&typeof value==='string'&&value.length===request.expected.selection?.source_code_units){wholeEncodes++;phase='whole';}
    else if(phase==='view_copy'&&typeof value==='string'){viewEncodes++;phase='view';}
    return bufferFrom.call(Buffer,value,...rest);
};
String.prototype.slice=function(a,b){const result=slice.call(this,a,b);if(phase==='selection'&&a===request.expected.selection.start_utf16&&b===request.expected.selection.end_utf16){viewCopies++;phase='view_copy';}return result;};
crypto.createHash=function(...args){const h=createHash(...args),update=h.update;h.update=function(value,...rest){
    if(Buffer.isBuffer(value)&&phase==='whole'){wholeHashes++;phase='selection';}
    else if(Buffer.isBuffer(value)&&phase==='view'){viewHashes++;phase='observed_view';}
    return update.call(this,value,...rest);};return h;};
String.prototype.codePointAt=function(at){phase='scan';codepoints++;
    if(!once&&request.fault?.startsWith('scan_')){once=true;
        if(request.fault==='scan_error')throw original;
        if(request.fault==='scan_deadline')clock=deadline;
        if(request.fault==='scan_dispose')owner.dispose();
        if(request.fault==='scan_reentry'){try{invoke();}catch{}}
    }
    return codepoint.call(this,at);
};
globalThis.__TEXT_WINDOW_MARKER=41;
try{
    if(request.expected_kind==='proxy')request.expected=new Proxy(request.expected,{ownKeys(){bad++;throw original;}});
    if(request.expected_kind==='accessor')Object.defineProperty(request.expected,'selection',{enumerable:true,get(){bad++;throw original;}});
    if(request.expected_kind==='cycle')request.expected.cycle=request.expected;
    if(request.expected_kind==='symbol')request.expected[Symbol('unexpected')]=0;
    if(request.owner_kind==='synthetic')owner=source.createCompilerEntryOwner(request.root,request.old_expected,request.profiles,request.old_schema,{now,deadline});
    else owner=(request.owner_kind==='foreign'?foreign:source).createCompilerTextWindowOwner(request.root,request.expected,request.profiles,request.schemas,{now,deadline});
    out.ready=owner.snapshot();
    if(request.old_borrow){let calls=0;try{source._borrowCompilerEntryText(owner,request.expected.entry,()=>{calls++;});}catch(e){out.old_borrow_failure=failure(e);}out.old_borrow_callbacks=calls;out.old_dispose=source._disposeCompilerEntry(owner);}
    if(request.legacy_surface){
        const req=request.legacy_surface;try{
            if(req.kind==='scalar')scalar.evaluateSyntheticCompilerEntry(owner,req.expected,req.profiles,req.schemas,{now,deadline});
            else if(req.kind==='environment')environment.evaluateSyntheticCompilerEnvironment(owner,req.expected,req.profiles,req.schemas,{now,deadline});
            else if(req.kind==='accessor')environment.evaluateSyntheticCompilerAccessorEnvironment(owner,req.expected,req.profiles,req.schemas,{now,deadline});
            else if(req.kind==='inventory')environment.evaluateSyntheticCompilerInventoryEnvironment(owner,req.expected,req.profiles,req.schemas,{now,deadline});
            else legacy[req.entrypoint](owner,req.expected,req.profiles,req.schemas,{now,deadline});
        }catch(e){out.legacy_failure=failure(e);}
    }
    if(request.worker_shape)out.worker_exports=Object.keys(kernel);
    let chosen=owner;
    if(request.owner_kind==='clone')chosen={...owner};
    if(request.owner_kind==='inherited')chosen=Object.create(owner);
    if(request.owner_kind==='proxy')chosen=new Proxy(owner,{get(){bad++;throw original;}});
    if(request.owner_kind==='fake')chosen={get snapshot(){bad++;throw original;}};
    if(request.owner_kind==='disposed')owner.dispose();
    phase='observation';try{out.result=invoke(chosen);}catch(e){out.failure=failure(e);}
    out.after=owner.snapshot();
    if(request.repeat){phase='repeat';try{invoke();}catch(e){out.repeat_failure=failure(e);}}
}catch(e){out.constructor_failure=failure(e);}
finally{if(owner){out.before_cleanup=owner.snapshot();owner.dispose();out.after_cleanup=owner.snapshot();}for(const fd of fds.keys()){native.closeSync(fd);out.emergency_close=true;}fds.clear();}
process.stdout.write(JSON.stringify({...out,opens,closes,entry_opens:entryOpens,open_fds:fds.size,forbidden_accesses:forbidden,forbidden_module_loads:moduleLoads,
    backend_invocations:backend,bad_inputs:bad,whole_encodes:wholeEncodes,whole_hashes:wholeHashes,view_copies:viewCopies,view_encodes:viewEncodes,view_hashes:viewHashes,
    codepoint_observations:codepoints,execution_marker:globalThis.__TEXT_WINDOW_MARKER,node_version:process.version})+'\n');

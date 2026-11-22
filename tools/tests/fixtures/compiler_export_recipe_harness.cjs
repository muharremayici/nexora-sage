'use strict';
// Owned synthetic inputs only. Backend spies must remain unused.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),vm=require('node:vm'),crypto=require('node:crypto');
const request=JSON.parse(fs.readFileSync(0,'utf8')),out={},fds=new Map();
const native=Object.fromEntries(['realpathSync','statSync','openSync','fstatSync','readSync','closeSync','readFileSync'].map(k=>[k,fs[k]]));
const source=require('../../core/compiler_entry_owner.cjs'),realBorrow=source._borrowCompilerEntryText,realDispose=source._disposeCompilerEntry;
const realFreeze=Object.freeze,get=Object.getOwnPropertyDescriptor,realCodePoint=String.prototype.codePointAt,realHash=crypto.createHash;
const realBufferFrom=Buffer.from,realSlice=String.prototype.slice;
const original=new RangeError('synthetic recipe fault'),cleanupOriginal=new URIError('synthetic cleanup fault');
let owner,observer,foreignObserver,token,phase='setup',clock=0,once=false,cleanupFaulted=false;
let opens=0,closes=0,entryOpens=0,forbidden=0,moduleLoads=0,backendCalls=0,codePoints=0,capsuleTicks=0,borrows=0,callbackResults=0,badInputs=0;
let wholeEncodings=0,wholeHashes=0,viewCopies=0,viewEncodings=0,viewHashes=0;
const sandbox=path.resolve(request.sandbox),entryPath=path.resolve(request.entry_path),deadline=1000;
const windowMode=request.model==='window',helper=request.model==='helper';
const entrypoint=windowMode?'observeSyntheticCompilerHelperWindow':helper?'observeSyntheticCompilerHelperSite':'observeSyntheticCompilerExportRecipe';
const consumeName=windowMode?'_consumeCompilerHelperWindowCapsule':helper?'_consumeCompilerHelperSiteCapsule':'_consumeCompilerExportRecipeCapsule';
const disposeName=windowMode?'_disposeCompilerHelperWindowCapsule':helper?'_disposeCompilerHelperSiteCapsule':'_disposeCompilerExportRecipeCapsule';
const other=request.other_model||(windowMode?'helper':helper?'recipe':'helper');
const otherConsume=other==='window'?'_consumeCompilerHelperWindowCapsule':other==='helper'?'_consumeCompilerHelperSiteCapsule':'_consumeCompilerExportRecipeCapsule';
const otherDispose=other==='window'?'_disposeCompilerHelperWindowCapsule':other==='helper'?'_disposeCompilerHelperSiteCapsule':'_disposeCompilerExportRecipeCapsule';
const otherEntrypoint=other==='window'?'observeSyntheticCompilerHelperWindow':other==='helper'?'observeSyntheticCompilerHelperSite':'observeSyntheticCompilerExportRecipe';
function invoke(chosen=owner,expected=request.recipe_expected){return observer[entrypoint](chosen,expected,request.profiles,request.schemas,{now,deadline});}
function failure(e){return {error:e instanceof observer.CompilerExportRecipeRefusal||e instanceof source.CompilerEntryRefusal
    ||(foreignObserver&&e instanceof foreignObserver.CompilerExportRecipeRefusal)?e.code:e===original?'injected_original':e?.name||String(e),
    stage:observer._compilerExportRecipeFailureStage(e),original_identity:e===original,cleanup_identity:e===cleanupOriginal,
    aggregate_primary_original:e instanceof AggregateError&&e.errors.includes(original),aggregate_cleanup_original:e instanceof AggregateError&&e.errors.includes(cleanupOriginal)};}
const now=()=>{
    if(phase==='capsule_minted'){
        capsuleTicks++;
        if((request.fault==='consume_dispose'&&capsuleTicks===3)||(request.fault==='consume_dispose_early'&&capsuleTicks===2))observer[disposeName](token);
        if(request.fault==='consume_reentry'&&capsuleTicks===3){try{observer[consumeName](token,request.recipe_expected);}catch{}}
        if(request.fault==='capsule_wrong_model'&&!once){
            once=true;out.wrong_model_disposed=observer[otherDispose](token);
            try{observer[otherConsume](token,{get observation_sha256(){badInputs++;throw original;}});}catch(e){out.wrong_model_failure=failure(e);}
        }
    }
    if(phase==='evaluation'&&request.fault==='initial_deadline')return deadline;
    if(phase==='evaluation'&&request.fault==='initial_clock_error')throw original;
    if(phase==='scan'&&request.fault==='scan_clock_error')throw original;
    if(phase==='publication'&&request.fault==='publication_deadline')return deadline;
    if(phase==='publication'&&request.fault==='publication_clock_error')throw original;
    if(phase==='publication'&&request.fault==='publication_reentry'&&!once){once=true;try{invoke();}catch{}}
    if(phase==='capsule_minted'&&!once&&request.fault?.startsWith('capsule_')){
        once=true;let chosen=token,e={...request.recipe_expected};
        if(request.fault==='capsule_dispose')observer[disposeName](token);
        else{
            if(request.fault==='capsule_wrong_identity')e.observation_sha256='0'.repeat(64);
            if(request.fault==='capsule_clone')chosen={...token};
            if(request.fault==='capsule_inherited')chosen=Object.create(token);
            if(request.fault==='capsule_proxy')chosen=new Proxy(token,{get(){badInputs++;throw original;}});
            if(request.fault==='capsule_fake')chosen={get observation(){badInputs++;throw original;}};
            try{out.external_consume=(request.fault==='capsule_foreign'?foreignObserver:observer)[consumeName](chosen,e);}
            catch(error){out.external_failure=failure(error);}
        }
    }
    return clock;
};
source._borrowCompilerEntryText=function(value,expected,callback){
    return realBorrow(value,expected,row=>{phase='borrow';borrows++;const result=callback(row);callbackResults+=result!==undefined?1:0;return result;});
};
source._disposeCompilerEntry=function(value){const result=realDispose(value);phase='cleanup';
    if(!cleanupFaulted&&['cleanup_error','scan_primary_cleanup'].includes(request.fault)){cleanupFaulted=true;throw cleanupOriginal;}return result;};
String.prototype.codePointAt=function(index){
    phase='scan';codePoints++;
    if(!once&&['scan_error','scan_primary_cleanup','scan_deadline','scan_dispose','scan_reentry','cross_model_reentry'].includes(request.fault)){
        once=true;if(['scan_error','scan_primary_cleanup'].includes(request.fault))throw original;
        if(request.fault==='scan_deadline')clock=deadline;
        if(request.fault==='scan_dispose')owner.dispose();
        if(request.fault==='scan_reentry'){try{invoke();}catch{}}
        if(request.fault==='cross_model_reentry'){try{observer[otherEntrypoint](owner,{},request.profiles,{}, {now,deadline});}catch(e){out.cross_model_failure=failure(e);}}
    }
    return realCodePoint.call(this,index);
};
Buffer.from=function(value,...rest){
    if(windowMode&&phase==='borrow'&&typeof value==='string'){
        if(wholeEncodings===0&&value.length===request.recipe_expected.selection.source_code_units){
            wholeEncodings++;if(request.fault==='whole_verification_error')throw original;
            if(request.fault==='whole_verification_dispose')owner.dispose();
            if(request.fault==='whole_verification_deadline')clock=deadline;
        }else if(wholeHashes===1&&viewCopies===1){viewEncodings++;}
    }
    return realBufferFrom.call(Buffer,value,...rest);
};
String.prototype.slice=function(start,end){
    if(windowMode&&phase==='borrow'&&wholeHashes===1&&String(this).length===request.recipe_expected.selection.source_code_units
        &&start===request.recipe_expected.selection.start_utf16&&end===request.recipe_expected.selection.end_utf16){
        viewCopies++;if(request.fault==='selection_copy_error')throw original;
        if(request.fault==='selection_copy_dispose')owner.dispose();
    }
    return realSlice.call(this,start,end);
};
crypto.createHash=function(...args){const h=realHash(...args),update=h.update;
    h.update=function(value,...rest){
        if(windowMode&&phase==='borrow'&&Buffer.isBuffer(value)){
            if(wholeEncodings===1&&wholeHashes===0&&value.length===request.owner_expected.entry_bytes)wholeHashes++;
            else if(viewCopies===1&&viewEncodings===1&&viewHashes===0)viewHashes++;
        }
        if(typeof value==='string'&&(value.startsWith('{"complete_recipe_within_grammar"')||value.startsWith('{"complete_site_syntax_within_grammar"')
            ||(windowMode&&value.startsWith('{"gap":')&&value.includes('"view_syntax_complete":')))){
        phase='projection';if(request.fault==='projection_error')throw original;
        if(request.fault==='projection_deadline')clock=deadline;
    }return update.call(this,value,...rest);};return h;};
Object.freeze=function(value){
    if(value&&['private_synthetic_export_recipe_capsule','private_synthetic_helper_site_capsule','private_synthetic_helper_window_capsule'].includes(get(value,'kind')?.value)){token=value;phase='capsule_minted';}
    if(value&&/STATIC_RECIPE|STATIC_HELPER_SITE|STATIC_HELPER_WINDOW/.test(get(value,'status')?.value||'')){phase='publication';out.unpublished_summary_seen=true;if(request.fault==='publication_error')throw original;}
    return realFreeze(value);
};
for(const key of ['compileFunction','createContext','runInContext','runInNewContext','runInThisContext','Script'])vm[key]=()=>{backendCalls++;throw Error('backend forbidden');};
observer=require('../../core/compiler_export_recipe.cjs');
if(request.fault==='capsule_foreign'){
    const file=require.resolve('../../core/compiler_export_recipe.cjs'),other=new Module(file);other.filename=file;other.paths=module.paths;
    other._compile(native.readFileSync(file,'utf8'),file);foreignObserver=other.exports;
}
let factory=source.createCompilerEntryOwner;
if(request.foreign_entry){const file=require.resolve('../../core/compiler_entry_owner.cjs'),other=new Module(file);other.filename=file;other.paths=module.paths;
    other._compile(native.readFileSync(file,'utf8'),file);factory=other.exports.createCompilerEntryOwner;}
function scoped(value){const full=path.resolve(String(value)),rel=path.relative(sandbox,full);if(rel==='..'||rel.startsWith('..'+path.sep)||path.isAbsolute(rel)){forbidden++;throw Error('outside fixture');}return full;}
fs.realpathSync=value=>{scoped(value);const result=native.realpathSync(value);scoped(result);return result;};
fs.statSync=value=>{scoped(value);return native.statSync(value);};
fs.openSync=(value,...args)=>{const full=scoped(value),fd=native.openSync(value,...args);fds.set(fd,full);opens++;if(full===entryPath)entryOpens++;return fd;};
fs.fstatSync=fd=>native.fstatSync(fd);fs.readSync=(...args)=>native.readSync(...args);
fs.closeSync=fd=>{native.closeSync(fd);fds.delete(fd);closes++;};
Module._load=()=>{moduleLoads++;throw Error('late module load forbidden');};
for(const key of ['readFileSync','readFile','open','readdirSync','readdir','writeFileSync','writeFile','appendFileSync'])fs[key]=()=>{forbidden++;throw Error('unowned IO');};
globalThis.__RECIPE_EXECUTION_MARKER=41;
try{
    owner=factory(request.root,request.owner_expected,request.profiles,request.owner_schema,{now,deadline});out.ready=owner.snapshot();
    let chosen=owner,expected=request.recipe_expected;
    if(request.owner_kind==='clone')chosen={...owner};
    if(request.owner_kind==='inherited')chosen=Object.create(owner);
    if(request.owner_kind==='proxy')chosen=new Proxy(owner,{get(){badInputs++;throw original;}});
    if(request.owner_kind==='fake')chosen={get snapshot(){badInputs++;throw original;}};
    if(request.owner_kind==='disposed')owner.dispose();
    if(request.expected_kind==='accessor')Object.defineProperty(expected,'observation_sha256',{enumerable:true,get(){badInputs++;throw original;}});
    if(request.expected_kind==='proxy')expected=new Proxy(expected,{ownKeys(){badInputs++;throw original;}});
    if(request.expected_kind==='cycle')expected.extra=expected;
    if(request.expected_kind==='symbol')expected[Symbol('unexpected')]=0;
    if(request.profile_kind==='proxy')request.profiles=new Proxy(request.profiles,{getOwnPropertyDescriptor(){badInputs++;throw original;}});
    if(request.profile_kind==='accessor')Object.defineProperty(request.profiles,windowMode?'private_compiler_helper_window':helper?'private_compiler_helper_site':'private_compiler_export_recipe',{get(){badInputs++;throw original;}});
    phase='evaluation';try{out.result=invoke(chosen,expected);}catch(error){out.failure=failure(error);}
    out.after=owner.snapshot();if(request.repeat){phase='repeat';try{invoke();}catch(error){out.repeat_failure=failure(error);}}
}catch(error){out.constructor_failure=failure(error);}
finally{if(owner)out.before_fixture_cleanup=owner.snapshot();owner?.dispose();for(const fd of [...fds.keys()]){native.closeSync(fd);fds.delete(fd);out.emergency_fixture_close=true;}}
process.stdout.write(JSON.stringify({...out,opens,closes,entry_opens:entryOpens,open_fds:fds.size,forbidden_accesses:forbidden,forbidden_module_loads:moduleLoads,
    backend_invocations:backendCalls,codepoint_observations:codePoints,entry_borrows:borrows,nonundefined_callback_results:callbackResults,bad_inputs:badInputs,
    whole_encoding_attempts:wholeEncodings,whole_hashes:wholeHashes,view_copy_attempts:viewCopies,view_encoding_attempts:viewEncodings,view_hashes:viewHashes,
    execution_marker:globalThis.__RECIPE_EXECUTION_MARKER,node_version:process.version})+'\n');

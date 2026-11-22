'use strict';
// First-party test adapter only. Retained entry files are never evaluated.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),Module=require('node:module');
const request=JSON.parse(fs.readFileSync(0,'utf8'));
const preparationSpy={attempted_roots:0,normal_returned_roots:0,threw_roots:0};
let laterContextValidations=0,preparationSnapshotCalls=0,preparationSnapshot=null,preparationSnapshotFailed=false;
const preparationSnapshots=new WeakSet();
let preparationBeforeClock=null,preparationMutationAttempted=false;
if(request.preparation_validation_probe){
    const shared=require('../../core/compiler_stream_stager.cjs'),nativeValidate=shared._validateCompilerStreamValue;
    const codes=new Set(['context_policy','context_intent','context_anchors','context_expected']);
    shared._validateCompilerStreamValue=function(...args){
        const preparation=codes.has(args[3]);
        if(preparation)preparationSpy.attempted_roots++;
        else if(typeof args[3]==='string'&&args[3].startsWith('context_'))laterContextValidations++;
        try{const result=nativeValidate(...args);if(preparation)preparationSpy.normal_returned_roots++;return result;}
        catch(error){if(preparation)preparationSpy.threw_roots++;throw error;}
    };
}
const copier=require('../../core/compiler_text_window.cjs'),nativeCopy=copier.copy;
let initialCopyCalls=0,initialCopyBudget=null,initialCopyAtExit=null,copySnapshotFailed=false,copyBudgetMutated=false;
let phaseAttempt=null,phaseRoot=null,phaseApi=0,phaseObservations=0,phaseFreezes=0,phaseWitnesses=0,phaseViews=0;
let phaseViewSame=false,phaseViewFrozen=false,phaseOutcomeInCopy=null,phaseBeforeClock=null,phaseFaults=0,phaseReleaseHook=false,phaseReentered=false;
copier.copy=function(...args){
    const phased=request.phased_copy===true&&args[1]==='comparison_capture_copy';
    if(args[1]!=='context_metadata'&&!phased)return nativeCopy(...args);
    initialCopyCalls++;initialCopyBudget=args[5];
    if(phased)phaseOutcomeInCopy=phaseAttempt?.copy_outcome??null;
    try{
        const value=nativeCopy(...args);
        if(phased){phaseRoot=value;if(request.phase_fault==='copy'){phaseFaults++;throw original;}}
        return value;
    }finally{
        initialCopyAtExit=initialCopyBudget?{nodes:initialCopyBudget.nodes,bytes:initialCopyBudget.bytes}:null;
    }
};
const source=require('../../core/compiler_entry_owner.cjs');let factory=source.createCompilerEntryOwner;
if(request.comparison_contract){
    // Authored fixture-only descriptor counterexample; no repository mutation.
    const schema=require('../../../config/schemas/compiler_entry_comparison_owner.schema.json');
    Object.assign(schema.$defs.contract.const,request.comparison_contract);
}
const context=require('../../core/compiler_entry_context_handoff.cjs');
const comparisonShared=require('../../core/compiler_stream_stager.cjs');
if(request.comparison_owner&&!request.comparison_skip_setup)comparisonShared._initializePhasedPreparationComparisonCapture();
const comparisonProbe={admissions:0,borrows:0,roots:[],native_from:0,native_compare:0,release_calls:0,snapshot_attempts:0};
let comparisonCandidate=null,comparisonLease=null,comparisonApi=null,comparisonActive=false,comparisonFaults=0,comparisonBeforeClock=null,comparisonUnready=null,externalComparisonError=null;
if(request.comparison_external_clock_error){try{comparisonShared._joinPhasedPreparationComparison(null);}catch(error){externalComparisonError=error;}}

let contextOnce=false,handoffFrozen=false;
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
const freeze=Object.freeze,alloc=Buffer.alloc,Decoder=global.TextDecoder,createHash=crypto.createHash,OriginalSet=global.Set;
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
    if(clockCalls===1&&request.comparison_owner){
        comparisonBeforeClock={lease:comparisonLease?.snapshot()??null,roots:[...comparisonProbe.roots]};
        if(request.comparison_external_clock_error)throw externalComparisonError;
    }
    if(clockCalls===1&&phaseAttempt)phaseBeforeClock={outcome:phaseAttempt.copy_outcome,witness_cleared:phaseAttempt.witness===null};
    if(request.phase_reentry&&!phaseReentered){
        phaseReentered=true;
        const other=request.phased_copy?context.observeSyntheticEntryOwnerContext:context._observeSyntheticEntryOwnerContextWithPhasedCopy;
        try{other(request.root,request.context_intent,request.context_anchors,request.profiles,
            {entry:request.schema,trace:request.trace_schema,handoff:request.context_schema},{deadline,now});}catch{}
    }
    if(clockCalls===1)preparationBeforeClock=preparationSnapshot!==null&&Object.isFrozen(preparationSnapshot);
    if(request.preparation_snapshot_mutation&&preparationSnapshot&&!preparationMutationAttempted){
        preparationMutationAttempted=true;try{preparationSnapshot.attempted_roots=0;}catch{}
    }
    if(request.copy_budget_mutation&&!copyBudgetMutated&&initialCopyBudget){copyBudgetMutated=true;initialCopyBudget.nodes=0;initialCopyBudget.bytes=0;}
    if(request.context_fault==='mutate_input'&&!contextOnce){contextOnce=true;request.context_intent.expected_context.entry_expected.entry_bytes=0;request.context_anchors.intent_sha256='0'.repeat(64);}
    if(request.context_fault==='caught_reentry'&&!contextOnce){contextOnce=true;try{context.observeSyntheticEntryOwnerContext(request.root,request.context_intent,request.context_anchors,request.profiles,{entry:request.schema,trace:request.trace_schema,handoff:request.context_schema},{deadline,now});}catch{}}
    if(request.context_fault==='primitive_clock')throw 0;
    if(request.context_fault==='proxy_error')throw new Proxy(original,{get(){getterCalls++;throw original;},getPrototypeOf(){getterCalls++;throw original;}});
    if(request.context_fault==='post_result_deadline'&&handoffFrozen)return deadline;
    if(request.context_fault==='post_result_reentry'&&handoffFrozen&&!contextOnce){contextOnce=true;try{context.observeSyntheticEntryOwnerContext(request.root,request.context_intent,request.context_anchors,request.profiles,{entry:request.schema,trace:request.trace_schema,handoff:request.context_schema},{deadline,now});}catch{}}

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
const nativeFrom=Buffer.from,nativeCompare=Buffer.compare,weakSet=WeakMap.prototype.set;
if(request.comparison_owner){
    Buffer.from=function(...args){
        if(comparisonActive){comparisonProbe.native_from++;
            if(request.comparison_native==='first'&&comparisonProbe.native_from===1||request.comparison_native==='second'&&comparisonProbe.native_from===2)throw comparisonThrown();}
        return nativeFrom.apply(this,args);
    };
    Buffer.compare=function(...args){if(comparisonActive){comparisonProbe.native_compare++;if(request.comparison_native==='compare')throw comparisonThrown();}return nativeCompare.apply(this,args);};
    WeakMap.prototype.set=function(key,value){
        if(value?.record?.version==='v1-private-synthetic-comparison-owner'){
            comparisonProbe.registration_unready=context._entryComparisonOwnerFailureObservation(key)===null;
            if(request.comparison_registration_fault){weakSet.call(this,key,value);comparisonUnready=key;throw original;}
        }
        return weakSet.call(this,key,value);
    };
}
function comparisonThrown(){
    if(request.comparison_error_kind==='primitive')return 0;
    if(request.comparison_error_kind==='nan')return NaN;
    if(request.comparison_error_kind==='proxy')return new Proxy(original,{get(){getterCalls++;throw original;},getPrototypeOf(){getterCalls++;throw original;}});
    return original;
}
Object.freeze=function(value){
    if(request.comparison_owner){
        if(typeof value?.borrow==='function'&&typeof value?.admit==='function'){
            comparisonLease=value;const borrow=value.borrow,admit=value.admit,release=value.release;
            value.borrow=function(...args){comparisonProbe.borrows++;const h=borrow.apply(this,args);comparisonProbe.same_held=h===phaseRoot;return h;};
            value.admit=function(...args){comparisonProbe.admissions++;return admit.apply(this,args);};
            value.release=function(...args){comparisonProbe.release_calls++;const r=release.apply(this,args);if(request.comparison_cleanup_fault&&comparisonFaults++===0)throw original;return r;};
        }
        if(typeof value?.validate_root==='function'){
            comparisonApi=value;const root=value.validate_root,snapshot=value.snapshot;
            value.validate_root=function(...args){comparisonProbe.roots.push(args[1]);comparisonActive=true;try{return root.apply(this,args);}finally{comparisonActive=false;}};
            value.snapshot=function(...args){comparisonProbe.snapshot_attempts++;if(request.comparison_snapshot_fault&&comparisonProbe.snapshot_attempts===2&&comparisonFaults++===0)throw original;return snapshot.apply(this,args);};
        }
        if(value?.version==='v1-private-synthetic-comparison-owner')comparisonCandidate=value;
        if(value?.version==='v1-private-synthetic-comparison-owner'&&request.comparison_publication_reentry&&!comparisonProbe.publication_reentry){
            comparisonProbe.publication_reentry=true;
            const fn=request.comparison_publication_reentry==='phased'?context._observeSyntheticEntryOwnerContextWithPhasedCopy:context.observeSyntheticEntryOwnerContext;
            try{fn(request.root,request.context_intent,request.context_anchors,request.profiles,
                {entry:request.schema,trace:request.trace_schema,handoff:request.context_schema},{deadline,now});}catch{}
        }
        if(value?.version==='v1-private-synthetic-comparison-owner'&&comparisonFaults===0){
            if(request.comparison_publication_fault){comparisonFaults++;throw original;}
            if(request.comparison_tamper){
                comparisonFaults++;
                if(request.comparison_tamper==='extra')value.extra=true;
                if(request.comparison_tamper==='flag')value.flags.cache_eligible=true;
                if(request.comparison_tamper==='count')value.comparison_observation={...value.comparison_observation,counters:{...value.comparison_observation.counters,admitted:9007199254740991}};
                if(request.comparison_tamper==='binding')value.declared_binding={...value.declared_binding,tree_sha256:'f'.repeat(64)};
                if(request.comparison_tamper==='reservation')value.logical_reservation.added_named_reserved_bytes=0;
            }
        }
    }
    if(request.phased_copy&&clockCalls===0){
        let stage=null;
        if(typeof value?.copy_input==='function'&&typeof value?.take_for_context_owner==='function'){phaseAttempt=value;phaseApi++;stage='api';}
        else if(value?.outcome&&Object.hasOwn(value,'nodes')&&Object.hasOwn(value,'scalar_key_charges')&&(!request.comparison_owner||phaseObservations===0)){phaseObservations++;stage='observation';}
        else if(phaseRoot!==null&&value===phaseRoot){phaseFreezes++;stage='tree';}
        else if(value?.held===phaseRoot&&Object.hasOwn(value,'witness')){phaseViews++;phaseViewSame=true;phaseViewFrozen=Object.isFrozen(phaseRoot);stage='view';}
        else if(typeof value?.toJSON==='function'&&!value?.dispose&&!value?.snapshot){phaseWitnesses++;stage='witness';}
        if(stage&&request.phase_release_stage===stage&&!phaseReleaseHook){phaseReleaseHook=true;phaseAttempt.release();}
        if(stage&&request.phase_fault===stage&&phaseFaults===0){phaseFaults++;throw original;}
    }
    if(value?.version==='v1-private-preparation-validation-roots'&&Object.hasOwn(value,'attempted_roots')&&!preparationSnapshots.has(value)){
        preparationSnapshots.add(value);
        preparationSnapshotCalls++;
        if(request.preparation_snapshot_fault&&!preparationSnapshotFailed){preparationSnapshotFailed=true;throw original;}
        preparationSnapshot=value;
    }
    if(request.copy_snapshot_fault&&!copySnapshotFailed&&value?.version==='v1-private-initial-context-copy-charges'){copySnapshotFailed=true;throw original;}
    if(value?.status==='SYNTHETIC_CONTEXT_TRACE_HANDOFF_COMPLETED_NOT_TRANSACTION_PROOF')handoffFrozen=true;
    if(constructing&&value?.snapshot&&value?.dispose&&value?.toJSON){
        captured=value;armed=true;if(request.fault==='owner_allocation_error')throw original;
    }
    return freeze(value);
};
Module._load=()=>{forbiddenModules++;throw Error('entry/module evaluation forbidden');};
if(request.phased_copy&&request.phase_fault==='setup')global.Set=function(){global.Set=OriginalSet;phaseFaults++;throw original;};
for(const key of ['readFileSync','readFile','open','readdirSync','readdir','writeFileSync','writeFile','appendFileSync'])fs[key]=()=>{forbidden++;throw Error('unowned fixture IO');};
let out={events:[]};
function error(value){
    const observation=context.entryContextHandoffFailureObservation(value);
    return {error:observation?.primary_code??null,original_identity:value===original,
        primitive_retained:request.context_fault==='primitive_clock'&&value===0,
        context_failure:observation,
        ...(request.comparison_owner?{comparison_failure:context._entryComparisonOwnerFailureObservation(value),
            comparison_primitive_retained:request.comparison_error_kind==='primitive'&&value===0,
            comparison_nan_retained:request.comparison_error_kind==='nan'&&Object.is(value,NaN),
            external_clock_identity:value===externalComparisonError}: {})};
}
try{
    if(request.context_fault==='copy_alias'){const leaf={a:1};request.context_intent.copy_probe={left:leaf,right:leaf};}
    if(request.context_fault==='copy_sparse')request.context_intent.copy_probe=Array(2);
    if(request.context_fault==='copy_prototype')request.context_intent.copy_probe=Object.assign(Object.create({inherited:true}),{a:1});
    if(request.context_fault==='getter_input')Object.defineProperty(request.context_intent,'expected_context',{enumerable:true,get(){getterCalls++;throw original;}});
    if(request.context_fault==='proxy_input')request.context_intent=new Proxy(request.context_intent,{get(){getterCalls++;throw original;},ownKeys(){getterCalls++;throw original;}});
    if(request.context_fault==='cycle_input')request.context_intent.cycle=request.context_intent;
    if(request.context_handoff){
        const selected=request.comparison_owner?context._observeSyntheticEntryOwnerContextWithComparison:request.phased_copy===true?context._observeSyntheticEntryOwnerContextWithPhasedCopy:context.observeSyntheticEntryOwnerContext;
        const args=[request.root,request.context_intent,request.context_anchors,request.profiles,
            {entry:request.schema,trace:request.trace_schema,handoff:request.context_schema},{deadline,now,...request.context_options}];
        if(request.comparison_owner){
            let binding={invocation_id:'a'.repeat(64),source_sha256:'c'.repeat(64),tree_sha256:'b'.repeat(64),cut:'one_live_phased_copy_six_preparation_roots',...request.comparison_binding};
            const caps={version:'v1-private-live-phased-comparison',authority_kind:'independent_declaration',invocation_id:'a'.repeat(64),source_sha256:'c'.repeat(64),cut:'one_live_phased_copy_six_preparation_roots',fuel:100000,payload:4194304,...request.comparison_caps};
            if(request.comparison_cap_hook)Object.defineProperty(caps,'fuel',{get(){getterCalls++;throw original;},enumerable:true});
            if(request.comparison_binding_proxy)binding=new Proxy(binding,{get(){getterCalls++;throw original;},getPrototypeOf(){getterCalls++;throw original;}});
            args.push(caps,binding);
        }
        if(request.context_extra_arg)args.push(true);
        const result=selected(...args);
        if(request.comparison_owner){out.comparison_outcome=result;out.context_handoff=result.base_handoff;}
        else out.context_handoff=result;
    }else if(request.trace){
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
    global.Set=OriginalSet;
    if(phaseAttempt){
        try{out.phased_attempt_snapshot=phaseAttempt.snapshot();}catch{out.phased_attempt_snapshot=null;out.phased_attempt_gap='unavailable';}
    }
    if(request.comparison_owner){
        if(request.comparison_debug)out.comparison_candidate=comparisonCandidate;
        out.comparison_probe={...comparisonProbe,before_clock:comparisonBeforeClock,lease_final:comparisonLease?.snapshot()??null,
            unready_lookup:comparisonUnready?context._entryComparisonOwnerFailureObservation(comparisonUnready):null};
        out.comparison_unknown_lookup=context._entryComparisonOwnerFailureObservation(new Proxy({code:'comparison_budget'},{get(){getterCalls++;throw original;}}));
    }
    if(owner)out.final_snapshot=owner.snapshot();
    if(captured&&!owner)out.unpublished_snapshot=captured.snapshot();
    owner?.dispose();
    for(const fd of [...fds.keys()]){native.closeSync(fd);fds.delete(fd);out.emergency_fixture_close=true;}
}
process.stdout.write(JSON.stringify({...out,node_version:process.version,forbidden_accesses:forbidden,forbidden_module_loads:forbiddenModules,
    preparation_validation_spy:preparationSpy,later_context_validations:laterContextValidations,
    preparation_snapshot_calls:preparationSnapshotCalls,preparation_snapshot_failed:preparationSnapshotFailed,
    preparation_before_clock:preparationBeforeClock,preparation_mutation_attempted:preparationMutationAttempted,
    initial_copy_invocations:initialCopyCalls,initial_copy_budget_at_exit:initialCopyAtExit,copy_snapshot_failed:copySnapshotFailed,copy_budget_mutated:copyBudgetMutated,
    phased_connection:{selected:request.phased_copy===true,api_allocations:phaseApi,observation_attempts:phaseObservations,
        root_freezes:phaseFreezes,witness_allocations:phaseWitnesses,view_attempts:phaseViews,same_owned_top:phaseViewSame,
        root_frozen_at_view:phaseViewFrozen,outcome_in_copy_call:phaseOutcomeInCopy,before_clock:phaseBeforeClock,
        faults:phaseFaults,release_hook:phaseReleaseHook,cross_entry_reentry:phaseReentered},
    opens,closes,entry_opens:entryOpens,entry_reads:entryReads,read_bytes:readBytes,open_fds:fds.size,
    consumer_calls:consumerCalls,bad_owner_calls:badOwnerCalls,getter_calls:getterCalls,entry_evaluated:globalThis.__ENTRY_OWNER_MARKER__===true})+'\n');

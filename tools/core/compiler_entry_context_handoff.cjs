'use strict';
// Private, source-fixed synthetic composition. No producer/callback/store injection.
const crypto=require('node:crypto'),{performance}=require('node:perf_hooks'),{types}=require('node:util');
const {copy}=require('./compiler_text_window.cjs');
const {_createPreparationCopyAttempt:createCopyAttempt,_preparationCaptureFailureCode:captureCode}=require('./compiler_preparation_capture.cjs');
const {_preparePhasedComparisonOwnerLease:prepareComparisonLease,_preparationComparisonFailureCode:comparisonCode}=require('./compiler_stream_stager.cjs');
const COMPARISON_SCHEMA=require('../../config/schemas/compiler_entry_comparison_owner.schema.json');
const COMPARISON=COMPARISON_SCHEMA.$defs.contract.const,comparisonFailures=new WeakMap();
const {observeSyntheticEntryOwnerLifetime:observeTrace,compilerEntryLifetimeTraceFailureObservation:traceFailure,
    CompilerEntryRefusal}=require('./compiler_entry_owner.cjs');
const {_canonicalCompilerStreamMetadata:rawCanonical,_validateCompilerStreamValue:validate}=require('./compiler_stream_stager.cjs');
const {_createEntryContextMetadataWorkMeter:createWorkMeter,EntryContextMetadataWorkRefusal}=require('./compiler_entry_metadata_work.cjs');
const PROFILE='private_compiler_entry_context_handoff',TRACE='private_compiler_entry_lifetime_trace';
const VERSION='v1-private-synthetic-entry-context-handoff',SCOPE='temporary_synthetic_entry_owner_lifetime_only';
const COMPLETE='SYNTHETIC_CONTEXT_TRACE_HANDOFF_COMPLETED_NOT_TRANSACTION_PROOF';
const FAILED='INCOMPLETE_SYNTHETIC_CONTEXT_TRACE_HANDOFF_NOT_SUCCESS';
const OFF=['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'];
const FLAGS=['input_origin_authenticated','source_freshness_verified','read_authority_granted','compiler_loaded',
    'input_code_evaluated','complete_cost_priced','physical_fit_verified','reservation_change_eligible',
    'production_enabled','semantic_lineage','cache_eligible','publication_authority'];
const canon=v=>active.work_meter.canonical(v);
const hash=v=>active.work_meter.hash(v);
const bytes=v=>active.work_meter.bytes(v);
let active=null;const failures=new WeakMap();
class EntryContextHandoffRefusal extends Error{constructor(code){super(code);this.code=code;}}
const need=(v,code)=>{if(!v)throw new EntryContextHandoffRefusal(code);};
const natural=v=>Number.isSafeInteger(v)&&v>=0;
const object=v=>v!==null&&(typeof v==='object'||typeof v==='function');
const keys=(v,ks,code)=>need(v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===[...ks].sort().join(','),code);
const checked=(v,definition,schema,code)=>{try{validate(v,definition,schema,code);}catch{throw new EntryContextHandoffRefusal(code);}};
// Fixed six preparation sites only; the existing validator and mapping run once.
function preparationChecked(v,definition,schema,code,site){
    const state=active.preparation_validation_state;
    state.attempted_roots++;
    try{
        const x=active.comparison;
        if(x){
            try{x.api.validate_root(x.api.context,site,x.binding);}
            catch(error){comparisonFailed(x,error,'root_validation');throw error;}
        }else checked(v,definition,schema,code);
        state.normal_returned_roots++;
    }
    catch(error){state.threw_roots++;throw error;}
}
function sealPreparationValidation(c){
    const state=c.preparation_validation_state;
    if(state===null)return;
    try{
        c.preparation_validation=Object.freeze({version:'v1-private-preparation-validation-roots',
            scope:'six_existing_preproducer_checked_roots_only',unit:'validation_root_calls',site_count:6,
            attempted_roots:state.attempted_roots,normal_returned_roots:state.normal_returned_roots,threw_roots:state.threw_roots,
            measurement_sealed:true,coverage_complete:false,recursive_validation_entries:null,equality_attempts:null,
            canonical_operand_utf8_bytes:null,complete_cost_priced:false,full_work_units:null,physical_fit:null});
        c.preparation_validation_gap=null;
    }catch{c.preparation_validation=null;c.preparation_validation_gap='snapshot_unavailable';}
    finally{c.preparation_validation_state=null;}
}
const freeze=v=>{if(v&&typeof v==='object'){for(const x of Object.values(v))freeze(x);Object.freeze(v);}return v;};
freeze(COMPARISON_SCHEMA);
function schemaSupported(schema,root=schema){
    const allowed=['$schema','$id','$defs','title','description','type','required','properties','additionalProperties',
        'const','enum','items','minItems','minLength','minimum','pattern','$ref'];
    need(schema&&typeof schema==='object'&&!Array.isArray(schema)&&Object.keys(schema).every(k=>allowed.includes(k)),'context_schema_vocabulary');
    if(Object.hasOwn(schema,'$ref'))need(Object.keys(schema).length===1&&typeof schema.$ref==='string'
        &&schema.$ref.startsWith('#/$defs/')&&Object.hasOwn(root.$defs,schema.$ref.slice(8)),'context_schema_vocabulary');
    if(Object.hasOwn(schema,'pattern')){try{need(typeof schema.pattern==='string','context_schema_vocabulary');new RegExp(schema.pattern,'u');}
        catch{throw new EntryContextHandoffRefusal('context_schema_vocabulary');}}
    for(const key of ['$defs','properties'])if(Object.hasOwn(schema,key)){
        need(schema[key]&&typeof schema[key]==='object'&&!Array.isArray(schema[key]),'context_schema_vocabulary');
        for(const value of Object.values(schema[key]))schemaSupported(value,root);
    }
    for(const key of ['items','additionalProperties'])if(schema[key]&&typeof schema[key]==='object')schemaSupported(schema[key],root);
}
function optionsOf(v){
    need(v&&typeof v==='object'&&!types.isProxy(v)&&!Array.isArray(v),'context_options');
    const ds=Object.getOwnPropertyDescriptors(v),ks=Reflect.ownKeys(ds);
    need(ks.every(k=>typeof k==='string'&&['deadline','now'].includes(k)&&Object.hasOwn(ds[k],'value')),'context_options');
    const out=Object.fromEntries(ks.map(k=>[k,ds[k].value]));
    need(typeof(out.now??performance.now)==='function'&&Number.isFinite(out.deadline),'context_deadline');
    return out;
}
function errorCode(error){
    if(!object(error)||types.isProxy(error))return null;
    const proto=Object.getPrototypeOf(error);
    if(proto===CompilerEntryRefusal.prototype||proto===EntryContextHandoffRefusal.prototype||proto===EntryContextMetadataWorkRefusal.prototype){
        const d=Object.getOwnPropertyDescriptor(error,'code');
        return d&&Object.hasOwn(d,'value')&&typeof d.value==='string'&&/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(d.value)?d.value:null;
    }
    for(const type of [TypeError,RangeError,SyntaxError,ReferenceError,URIError,EvalError,Error]){
        if(proto===type.prototype)return type.name;
    }
    return null;
}
function policyOf(held){
    const {intent:i,anchors:a,profiles:p,schemas:s}=held,d=p[PROFILE],t=p[TRACE];
    keys(p,[PROFILE,TRACE,'private_compiler_entry_owner','compiler_library_inputs','private_input_image_experiment'],'context_profiles');
    keys(s,['handoff','trace','entry'],'context_schemas');
    schemaSupported(s.handoff);
    if(active.comparison){
        const x=active.comparison;
        try{
            x.api=x.lease.admit(x.declaration,x.binding);
            // Distinct metadata role: price the declared cumulative native pair
            // allowance before the first root. This is not a receipt retry.
            const admission=x.api.snapshot(),q=admission.capture_binding.exact_quota_declaration;
            const pairs=Math.min(q.payload,2*admission.copy_charges.scalar_key_charges*q.fuel);
            need(natural(pairs)&&pairs<=COMPARISON.pair_payload_limit_bytes
                &&natural(x.reserved+pairs)&&x.reserved+pairs<=COMPARISON.added_named_limit_bytes,'comparison_owner_budget');
            x.reserved+=pairs;x.pair_reserved=pairs;
        }
        catch(error){comparisonFailed(x,error,'admission');throw error;}
        finally{x.declaration=null;}
    }
    preparationChecked(d,s.handoff.$defs.policy,s.handoff,'context_policy','d');
    const initial=d.initial_copy_observation;
    need(initial.version==='v1-private-initial-context-copy-charges'&&initial.scope==='one_initial_owned_context_copy_only'
        &&initial.unit==='partial_copy_admission_charges'&&initial.state_bytes===512&&initial.snapshot_bytes===2048,'context_policy');
    const preparation=d.preparation_validation_observation;
    need(preparation.version==='v1-private-preparation-validation-roots'
        &&preparation.scope==='six_existing_preproducer_checked_roots_only'&&preparation.unit==='validation_root_calls'
        &&preparation.site_count===6&&preparation.state_bytes===512&&preparation.snapshot_bytes===1024,'context_policy');
    preparationChecked(t,s.trace.$defs.policy,s.trace,'context_policy','t');
    need(d.version===VERSION&&d.scope===SCOPE&&d.result_status===COMPLETE&&d.failure_status===FAILED
        &&t.version==='v1-private-synthetic-entry-owner-trace'&&t.scope===SCOPE
        &&canon(d.denied_flags)===canon(FLAGS),'context_policy');
    need([d,t,p.private_compiler_entry_owner].every(v=>OFF.every(k=>v[k]===false))
        &&p.private_input_image_experiment.default_enabled===false&&p.private_input_image_experiment.production_enabled===false,'context_policy');
    need(d.schema_sha256===hash(s.handoff)&&d.trace_policy_sha256===hash(t)&&d.trace_schema_sha256===t.schema_sha256
        &&t.schema_sha256===hash(s.trace)&&d.owner_schema_sha256===t.owner_schema_sha256&&t.owner_schema_sha256===hash(s.entry)
        &&d.trace_source_sha256===t.source_sha256&&d.owner_source_sha256===t.owner_source_sha256
        &&t.owner_policy_sha256===hash(p.private_compiler_entry_owner),'context_policy');
    const L=d.limits;
    keys(L,['context_bytes','controller_context_bytes','failure_bytes','envelope_bytes','output_bytes','controller_output_bytes','named_bytes','operation_ms'],'context_policy');
    need(Object.values(L).every(v=>natural(v)&&v>0)&&L.context_bytes<=65536&&L.controller_context_bytes<=131072
        &&L.failure_bytes<=32768&&L.envelope_bytes<=36864&&L.output_bytes<=65536&&L.controller_output_bytes<=32768
        &&L.named_bytes<=t.limits.image_bytes&&L.operation_ms<=t.limits.operation_ms
        &&natural(t.limits.events)&&t.limits.events<=128&&natural(t.limits.role_bytes)&&t.limits.role_bytes<=65536,
        'context_policy');
    preparationChecked(i,s.handoff.$defs.intent,s.handoff,'context_intent','i');
    preparationChecked(a,s.handoff.$defs.anchors,s.handoff,'context_anchors','a');
    preparationChecked(i.expected_context,s.trace.$defs.expected,s.trace,'context_expected','i.expected_context');
    preparationChecked(i.expected_context.entry_expected,s.entry.$defs.expected,s.entry,'context_expected','i.expected_context.entry_expected');
    need(i.version===d.intent_version&&i.purpose===d.purpose&&i.scope===SCOPE,'context_intent');
    const x=i.expected_context;
    need(x.policy_sha256===hash(t)&&x.schema_sha256===hash(s.trace)&&x.owner_source_sha256===t.owner_source_sha256
        &&x.entry_expected.policy_sha256===t.owner_policy_sha256&&x.entry_expected.schema_sha256===t.owner_schema_sha256
        &&x.entry_expected.compiler_version===t.synthetic_version&&natural(x.entry_expected.entry_bytes)
        &&x.entry_expected.entry_bytes<=t.limits.role_bytes,'context_expected');
    const expected={intent_sha256:hash(i),expected_context_sha256:hash(x),attempt_id:i.attempt_id,scope:SCOPE,
        handoff_policy_sha256:hash(d),handoff_schema_sha256:hash(s.handoff),handoff_source_sha256:d.source_sha256,
        trace_policy_sha256:hash(t),trace_schema_sha256:hash(s.trace),trace_source_sha256:t.source_sha256,
        owner_source_sha256:t.owner_source_sha256};
    need(canon(a)===canon(expected),'context_anchor_mismatch');
    return {d,t,s,i,a:expected};
}
function reserve(held,d,t,s){
    const L=d.limits,C=bytes(held),I=bytes(held.intent),n=Object.keys(t.role_paths).length;
    need(C<=L.context_bytes&&n===5,'context_budget');
    const terms=d.reservation_terms;
    need(canon(terms)===canon({metadata_utf8_and_utf16:3,held_context_copies:2,controller_intent_copies:2,
        simultaneous_trace_raw_forms:3,per_role_eof_bytes:1,projection_metadata_copies:2}),'context_policy');
    // Source bound: trace metadata+C <= metadata limit; known = metadata+C+3*raw.
    // This uses declared bounds, not another implementation of trace preflight.
    const producer=t.limits.metadata_bytes+terms.simultaneous_trace_raw_forms*n*(t.limits.role_bytes+terms.per_role_eof_bytes);
    const event={sequence:Number.MAX_SAFE_INTEGER,kind:s.trace.$defs.event.properties.kind.enum.reduce((a,b)=>a.length>=b.length?a:b),
        role:'entry',first:Number.MAX_SAFE_INTEGER,second:Number.MAX_SAFE_INTEGER};
    const failure={status:'INCOMPLETE_SYNTHETIC_OWNER_TRACE_NOT_SUCCESS',events:[],recording_incomplete:false,
        owner_released:false,close_unknown:false,role_opens:5,role_closes:5,owner_scope_complete:false,
        transaction_settled:false,compiler_loaded:false,physical_fit:null};
    const failureUpper=bytes(failure)+t.limits.events*(bytes(event)+1);
    const sourceUpper=Math.max(t.limits.output_bytes,failureUpper);
    need(d.linkage_overhead_bytes===4096&&natural(d.projection_output_bound_bytes)&&natural(d.projection_metadata_bound_bytes)
        &&failureUpper<=L.failure_bytes&&sourceUpper+d.linkage_overhead_bytes<=L.envelope_bytes
        &&L.envelope_bytes+d.linkage_overhead_bytes<=L.output_bytes
        &&d.projection_output_bound_bytes+d.linkage_overhead_bytes<=L.controller_output_bytes,'context_budget');
    const extra=terms.metadata_utf8_and_utf16*(terms.held_context_copies*C+terms.controller_intent_copies*I
        +sourceUpper+L.envelope_bytes+L.output_bytes+L.controller_context_bytes+L.controller_output_bytes
        +terms.projection_metadata_copies*d.projection_metadata_bound_bytes+d.projection_output_bound_bytes
        +d.metadata_work.state_bytes+2*d.metadata_work.snapshot_bytes
        +d.initial_copy_observation.state_bytes+2*d.initial_copy_observation.snapshot_bytes
        +d.preparation_validation_observation.state_bytes+2*d.preparation_validation_observation.snapshot_bytes);
    const total=producer+extra;
    need([producer,extra,total].every(natural)&&total<=L.named_bytes,'context_budget');
    return {producer_reserved_bound_bytes:producer,added_named_reserved_bytes:extra,known_named_reserved_bytes:total};
}
function observeSyntheticEntryOwnerContext(root,intentInput,anchorsInput,profilesInput,schemasInput,optionsInput){
    return observeEntryContext(root,intentInput,anchorsInput,profilesInput,schemasInput,optionsInput,false);
}
function observeSyntheticEntryOwnerContextWithPhasedCopy(root,intentInput,anchorsInput,profilesInput,schemasInput,optionsInput){
    return observeEntryContext(root,intentInput,anchorsInput,profilesInput,schemasInput,optionsInput,true);
}
function observeSyntheticEntryOwnerContextWithComparison(root,intent,anchors,profiles,schemas,options,declaration,binding){
    need(arguments.length===8,'comparison_owner_arguments');
    let input={declaration,binding};
    try{return observeEntryContext(root,intent,anchors,profiles,schemas,options,true,input);}
    finally{input=declaration=binding=null;}
}
function observeEntryContext(root,intentInput,anchorsInput,profilesInput,schemasInput,optionsInput,phasedCopy,comparisonInput=null){
    if(active){active.poisoned=true;throw new EntryContextHandoffRefusal('context_reentry');}
    const c={poisoned:false,producer_calls:0,producer_outcome:'not_started',intent_sha256:null,expected_context_sha256:null,
        attempt_id:null,envelope:null,envelope_sha256:null,anchors:null,reservation:null,metadata_gap:'not_prepared',
        work_meter:null,direct_metadata_work:null,terminal_forms_used:0,terminal_output_bytes:null,terminal_failure_bytes:null,
        root_copy_outcome:'not_started',initial_copy_charges:null,copy_observation_gap:'not_started',
        preparation_validation_state:{attempted_roots:0,normal_returned_roots:0,threw_roots:0},
        preparation_validation:null,preparation_validation_gap:'not_sealed',comparison:null};
    active=c;let held=null,primary=null,result=null,sourceResult=null,context=null,copyAttempt=null,failed=false;
    try{
        const options=optionsOf(optionsInput);
        if(comparisonInput)c.comparison=createComparisonState(comparisonInput);
        if(phasedCopy){
            let copyInput=null,copyObservation=null;
            try{
                copyInput={intent:intentInput,anchors:anchorsInput,profiles:profilesInput,schemas:schemasInput};
                copyAttempt=createCopyAttempt();
                copyAttempt.copy_input(copyInput);
            }catch{throw new EntryContextHandoffRefusal('context_metadata');}
            finally{
                // Source-owned scalar projection: no snapshot allocation or retry.
                if(copyAttempt!==null)c.root_copy_outcome=copyAttempt.copy_outcome;
                if(c.root_copy_outcome!=='not_started'){
                    try{
                        copyObservation=copyAttempt.copy_observation();
                        need(natural(copyObservation.nodes)&&natural(copyObservation.scalar_key_charges),'context_copy_observation');
                        c.initial_copy_charges=Object.freeze({version:'v1-private-initial-context-copy-charges',
                            scope:'one_initial_owned_context_copy_only',unit:'partial_copy_admission_charges',
                            helper_node_charges:copyObservation.nodes,logical_scalar_key_byte_charges:copyObservation.scalar_key_charges,
                            ceiling:65536,coverage_complete:false,complete_cost_priced:false,full_work_units:null,physical_fit:null});
                        c.copy_observation_gap=null;
                    }catch{c.initial_copy_charges=null;c.copy_observation_gap='snapshot_unavailable';}
                }
                copyInput=copyObservation=null;
            }
        }else{
        let copyInput=null,copyBudget=null,copySeen=null;
        try{
            copyInput={intent:intentInput,anchors:anchorsInput,profiles:profilesInput,schemas:schemasInput};
            copyBudget={nodes:0,bytes:0};copySeen=new Set();
            c.root_copy_outcome='threw';
            held=copy(copyInput,'context_metadata',65536,copySeen,0,copyBudget);
            c.root_copy_outcome='returned';
        }catch{throw new EntryContextHandoffRefusal('context_metadata');}
        finally{
            if(c.root_copy_outcome!=='not_started'){
                try{
                    need(natural(copyBudget.nodes)&&natural(copyBudget.bytes),'context_copy_observation');
                    c.initial_copy_charges=Object.freeze({version:'v1-private-initial-context-copy-charges',
                        scope:'one_initial_owned_context_copy_only',unit:'partial_copy_admission_charges',
                        helper_node_charges:copyBudget.nodes,logical_scalar_key_byte_charges:copyBudget.bytes,
                        ceiling:65536,coverage_complete:false,complete_cost_priced:false,full_work_units:null,physical_fit:null});
                    c.copy_observation_gap=null;
                }catch{c.initial_copy_charges=null;c.copy_observation_gap='snapshot_unavailable';}
            }
            copyInput=copyBudget=copySeen=null;
        }
        }
        // Capture is inaccessible to the supplied test clock before its first call.
        let preparationFailed=false;
        try{
            if(phasedCopy){
                copyAttempt.freeze_owned();
                if(c.comparison){
                    const x=c.comparison;
                    try{
                        x.lease=prepareComparisonLease(copyAttempt,copyAttempt.witness);copyAttempt=null;
                    }catch(error){comparisonFailed(x,error,'capture_transfer');throw error;}
                    try{held=x.lease.borrow();x.base_schema=held.schemas.handoff;}
                    catch(error){comparisonFailed(x,error,'prefix_borrow');throw error;}
                }else{
                    const view=copyAttempt.take_for_context_owner(copyAttempt.witness);
                    held=view.held;
                    const attempt=copyAttempt;copyAttempt=null;attempt.release();
                }
            }else freeze(held);
            c.work_meter=createWorkMeter(held.profiles[PROFILE].metadata_work,held.profiles[PROFILE].limits.output_bytes);
            c.terminal_output_bytes=held.profiles[PROFILE].limits.output_bytes;
            c.terminal_failure_bytes=held.profiles[PROFILE].metadata_work.terminal_failure_bytes;
            context=policyOf(held);
        }
        catch(error){
            preparationFailed=true;
            if(c.comparison?.failed&&Object.is(error,c.comparison.primary))throw error;
            if(error instanceof EntryContextHandoffRefusal||error instanceof EntryContextMetadataWorkRefusal)throw error;
            throw new EntryContextHandoffRefusal('context_policy');
        }
        finally{
            if(c.comparison)settleComparison(c.comparison);
            sealPreparationValidation(c);
            if(c.comparison?.cleanup_failed&&!preparationFailed){
                comparisonFailed(c.comparison,c.comparison.cleanup_error,'settlement');throw c.comparison.cleanup_error;
            }
        }
        const {d,t,s,i,a}=context;
        c.intent_sha256=a.intent_sha256;c.expected_context_sha256=a.expected_context_sha256;c.attempt_id=i.attempt_id;c.anchors=a;
        const now=options.now??(()=>performance.now()),deadline=options.deadline;
        let last=now();const start=last;
        need(Number.isFinite(start)&&start<deadline&&deadline<=start+d.limits.operation_ms,'context_deadline');
        const tick=()=>{const at=now();need(active===c&&!c.poisoned,'context_reentry');
            need(Number.isFinite(at)&&at>=last&&at<deadline,'context_deadline');last=at;return at;};
        tick();c.reservation=reserve(held,d,t,s);
        if(c.comparison)need(natural(c.reservation.known_named_reserved_bytes+c.comparison.reserved)
            &&c.reservation.known_named_reserved_bytes+c.comparison.reserved<=d.limits.named_bytes,'comparison_owner_budget');
        tick();c.metadata_gap='producer_not_returned';
        const p=Object.fromEntries([TRACE,'private_compiler_entry_owner','compiler_library_inputs','private_input_image_experiment'].map(k=>[k,held.profiles[k]]));
        c.work_meter.markProducerEntry();c.producer_calls=1;c.producer_outcome='failed';
        sourceResult=observeTrace(root,i.expected_context,p,{trace:s.trace,entry:s.entry},{now:tick,deadline});
        c.producer_outcome='completed';c.metadata_gap='completion_metadata_unavailable';
        checked(sourceResult,s.trace.$defs.result,s.trace,'context_source_result');tick();
        c.envelope={evidence_ref_sha256:a.intent_sha256,expected_context_sha256:a.expected_context_sha256,
            outcome:'completed',trace:sourceResult,error:null};
        need(bytes(c.envelope)<=d.limits.envelope_bytes,'context_envelope_budget');
        c.envelope_sha256=hash(c.envelope);c.metadata_gap=null;
        c.direct_metadata_work=c.work_meter.seal();
        result={version:VERSION,status:COMPLETE,scope:SCOPE,attempt_id:c.attempt_id,anchors:a,
            envelope:c.envelope,envelope_sha256:c.envelope_sha256,producer_calls:1,producer_outcome:'completed',
            reservation:c.reservation,reservation_basis:d.reservation_basis,references_cleared:true,new_deadline:false,
            transaction_settled:false,input_origin_attested:false,full_work_units:null,physical_fit:null,
            flags:Object.fromEntries(FLAGS.map(k=>[k,false])),direct_metadata_work:c.direct_metadata_work,
            root_copy_outcome:c.root_copy_outcome,initial_copy_charges:c.initial_copy_charges,copy_observation_gap:c.copy_observation_gap,
            preparation_validation:c.preparation_validation,preparation_validation_gap:c.preparation_validation_gap};
        checked(result,s.handoff.$defs.result,s.handoff,'context_result');
        terminalSize(result,c);freeze(result);tick();
    }catch(error){
        primary=error;failed=true;
        if(c.producer_calls===1&&c.producer_outcome==='failed'&&context){
            try{
                const code=errorCode(primary),observation=traceFailure(primary);
                if(code&&observation){
                    const {d,s}=context;
                    checked(observation,s.handoff.$defs.trace_failure,s.handoff,'context_failure_metadata');
                    need(Array.isArray(observation.events)&&observation.events.length<=context.t.limits.events,'context_failure_metadata');
                    need(bytes(observation)<=d.limits.failure_bytes,'context_failure_metadata');
                    c.envelope={evidence_ref_sha256:c.intent_sha256,expected_context_sha256:c.expected_context_sha256,
                        outcome:'failed',trace:observation,error:{code,original_identity_retained:true}};
                    need(bytes(c.envelope)<=d.limits.envelope_bytes,'context_failure_metadata');
                    c.envelope_sha256=hash(c.envelope);c.metadata_gap=null;
                }else c.metadata_gap='original_failure_metadata_unavailable';
            }catch{c.envelope=null;c.envelope_sha256=null;c.metadata_gap='secondary_failure_metadata_unavailable';}
        }
    }finally{
        const attempt=copyAttempt;copyAttempt=null;
        if(attempt!==null){try{attempt.release();}catch{/* Preserve the owning boundary's primary. */}}
        if(c.comparison&&!c.comparison.settled)settleComparison(c.comparison);
        sealPreparationValidation(c);
        if(c.work_meter)c.direct_metadata_work=c.work_meter.seal();
        c.work_meter=null;
        held=sourceResult=context=null;
        if(active===c&&!c.comparison)active=null;
        if(comparisonInput){comparisonInput.declaration=comparisonInput.binding=null;comparisonInput=null;}
    }
    if(failed){
        if(object(primary)){
            // Never copy arbitrary exception properties, messages, stacks or paths.
            const record={version:VERSION,status:FAILED,scope:SCOPE,attempt_id:c.attempt_id,
                anchors:c.anchors,envelope:c.envelope,envelope_sha256:c.envelope_sha256,
                producer_calls:c.producer_calls,producer_outcome:c.producer_outcome,reservation:c.reservation,
                metadata_gap:c.metadata_gap,primary_code:errorCode(primary),original_identity_retained:true,
                references_cleared:true,new_deadline:false,transaction_settled:false,physical_fit:null,
                direct_metadata_work:c.direct_metadata_work,root_copy_outcome:c.root_copy_outcome,
                initial_copy_charges:c.initial_copy_charges,copy_observation_gap:c.copy_observation_gap,
                preparation_validation:c.preparation_validation,preparation_validation_gap:c.preparation_validation_gap};
            // Terminal work was reserved before metered body effects. A secondary
            // serialization failure must not replace the primary or invent a receipt.
            try{terminalSize(record,c,true);failures.set(primary,freeze(record));}catch{}
        }
        if(c.comparison){
            publishComparisonFailure(c.comparison,primary,object(primary)?failures.get(primary)??null:null);
            clearComparison(c.comparison);c.comparison=null;
            if(active===c)active=null;
        }
        throw primary;
    }
    if(c.comparison){
        try{
            const local=comparisonOutcome(c.comparison,result,false,null);
            need(active===c&&!c.poisoned,'context_reentry');return local;
        }
        catch(error){comparisonFailed(c.comparison,error,'publication');publishComparisonFailure(c.comparison,error,result);throw error;}
        finally{clearComparison(c.comparison);c.comparison=null;if(active===c)active=null;}
    }
    return result;
}
function createComparisonState(input){
    const d=COMPARISON;
    const reserved=d.utf8_utf16_factor*(d.held_reference_model_bytes+d.state_model_bytes+2*d.snapshot_bytes+d.binding_bytes
        +d.terminal_forms*(d.base_bytes+d.output_bytes));
    need(natural(reserved)&&reserved<=d.added_named_limit_bytes,'comparison_owner_budget');
    return {declaration:input.declaration,binding:input.binding,lease:null,api:null,snapshot:null,gap:'not_started',
        settled:false,cleanup:'not_started',cleanup_failed:false,cleanup_error:null,failed:false,primary:null,stage:null,
        forms:0,reserved,pair_reserved:0,base_schema:null};
}
function comparisonFailed(x,error,stage){
    // Establish exact call provenance before any fallible lookup/publication.
    if(!x.failed){x.failed=true;x.primary=error;x.stage=stage;}
}
function settleComparison(x){
    if(x.settled)return;
    x.settled=true;
    try{
        if(x.lease===null){x.gap='lease_unavailable';return;}
        try{x.lease.release();x.cleanup='settled';}
        catch(error){x.cleanup='unavailable';x.cleanup_failed=true;x.cleanup_error=error;}
        if(x.api===null)x.gap='comparison_unavailable';
        else{
            try{x.snapshot=x.api.snapshot();x.gap=null;}
            catch{x.snapshot=null;x.gap='snapshot_unavailable';}
        }
        if(x.cleanup_failed)x.gap='cleanup_unavailable';
    }finally{x.lease=x.api=x.declaration=x.binding=null;}
}
function comparisonOutcome(x,base,failed,primary){
    // Source-owned fixed shapes bound each terminal form before canonical work.
    need(x.forms<COMPARISON.terminal_forms,'comparison_owner_terminal_budget');x.forms++;
    let code=null,kind=null;
    if(failed){
        if(x.failed&&Object.is(primary,x.primary)){
            try{code=comparisonCode(primary);if(code!==null)kind='comparison';
                else{code=captureCode(primary);if(code!==null)kind='capture';}}
            catch{code=null;}
        }
        if(kind===null){kind='native_or_context';try{code=errorCode(primary);}catch{code=null;}}
    }
    if(base!==null){
        if(x.base_schema===null)base=null;
        else validate(base,x.base_schema.$defs[base.status===COMPLETE?'result':'failure'],x.base_schema,'comparison_owner_base');
    }
    const result={version:COMPARISON.version,scope:COMPARISON.scope,outcome:failed?'failed':'completed',
        base_handoff:base,base_gap:base===null?'base_observation_unavailable':null,
        declared_binding:x.snapshot?.declared_binding??null,comparison_observation:x.snapshot,comparison_gap:x.gap,
        primary_stage:failed?(x.failed&&Object.is(primary,x.primary)?x.stage:'owner_body'):null,
        primary_kind:kind,primary_code:code,cleanup_state:x.cleanup,
        logical_reservation:{added_named_reserved_bytes:x.reserved,comparison_pair_reserved_bytes:x.pair_reserved,terminal_forms_used:x.forms,
            terminal_forms_reserved:COMPARISON.terminal_forms,terminal_output_bound_bytes:COMPARISON.output_bytes,
            complete_cost_priced:false,physical_fit:null},flags:Object.fromEntries(FLAGS.map(k=>[k,false])),
        full_work_units:null,physical_fit:null};
    // Base shapes were independently checked by the original owner. Missing
    // failure metadata stays null; comparison fields do not extend that schema.
    freeze(result);
    validateComparisonOutcome(result,x);
    need(Buffer.byteLength(rawCanonical(result),'utf8')<=COMPARISON.output_bytes,'comparison_owner_output_budget');
    return result;
}
function validateComparisonOutcome(result,x){
    validate(result,COMPARISON_SCHEMA.$defs.outcome,COMPARISON_SCHEMA,'comparison_owner_outcome');
    const L=result.logical_reservation;
    need(L.added_named_reserved_bytes===x.reserved&&L.comparison_pair_reserved_bytes===x.pair_reserved&&L.terminal_forms_used===x.forms
        &&L.terminal_forms_reserved===COMPARISON.terminal_forms&&L.terminal_output_bound_bytes===COMPARISON.output_bytes,
        'comparison_owner_outcome');
    const definition=COMPARISON_SCHEMA.$defs.outcome.properties;
    const nullableObject=(value,schema)=>{if(value!==null)validate(value,{...schema,type:'object'},COMPARISON_SCHEMA,'comparison_owner_outcome');};
    nullableObject(result.declared_binding,definition.declared_binding);
    const s=result.comparison_observation;
    nullableObject(s,definition.comparison_observation);
    if(s!==null){
        for(const key of ['counters','roots','refused_dimensions','recorded_prefix'])
            nullableObject(s[key],definition.comparison_observation.properties[key]);
    }
    if(s===null){need(result.declared_binding===null&&result.comparison_gap!==null,'comparison_owner_outcome');return;}
    need(['invocation_id','source_sha256','tree_sha256','cut'].every(k=>result.declared_binding?.[k]===s.declared_binding[k]),'comparison_owner_outcome');
    if(result.cleanup_state==='settled')need(s.phase==='SEALED'&&s.references_cleared,'comparison_owner_outcome');
    const c=s.counters,r=s.roots,q=s.capture_binding.exact_quota_declaration,B=s.copy_charges.scalar_key_charges;
    need(natural(B)&&B<=65536&&natural(s.copy_charges.nodes)&&s.copy_charges.nodes>=1&&s.copy_charges.nodes<=65536,'comparison_owner_outcome');
    if(c!==null){
        need(Object.values(c).every(natural)&&c.entered===c.admitted+c.denied&&c.entered<=q.fuel+1&&c.admitted<=q.fuel
            &&c.denied<=1&&c.reserved_payload===2*B*c.admitted&&c.reserved_payload<=q.payload
            &&c.reserved_payload<=x.pair_reserved
            &&c.buffer_requests===c.buffer_returns+c.buffer_unknown&&c.buffer_requests<=2*c.admitted
            &&c.compare_requests===c.compare_returns+c.compare_unknown&&c.compare_requests<=c.admitted,'comparison_owner_outcome');
    }
    if(r!==null){
        need(Object.values(r).every(natural)&&r.entered<=6&&r.threw<=1&&r.returned+r.threw===r.entered,'comparison_owner_outcome');
        const p=result.base_handoff?.preparation_validation;
        if(p)need(p.attempted_roots===r.entered&&p.normal_returned_roots===r.returned&&p.threw_roots===r.threw,'comparison_owner_outcome');
    }
}

function publishComparisonFailure(x,error,base){
    if(!object(error))return;
    try{
        const record=comparisonOutcome(x,base,true,error),row={record,ready:false};
        comparisonFailures.set(error,row);row.ready=true;
    }catch{/* Original primary survives unavailable diagnostic publication. */}
}
function clearComparison(x){
    x.primary=x.cleanup_error=x.snapshot=x.declaration=x.binding=x.lease=x.api=x.base_schema=null;
}
function comparisonOwnerFailureObservation(error){
    const row=comparisonFailures.get(error);
    return row&&row.ready?structuredClone(row.record):null;
}

function terminalSize(value,c,failure=false){
    const ceiling=(failure?c.terminal_failure_bytes:c.terminal_output_bytes)??65536;
    c.terminal_forms_used++;
    need(c.terminal_forms_used<=2,'context_terminal_budget');
    // Counters are sealed before serializing themselves. This one existing
    // output size pass is reserved, explicitly unmeasured, and never hashed.
    const size=Buffer.byteLength(rawCanonical(value),'utf8');
    need(size<=ceiling,'context_output_budget');return size;
}
function entryContextHandoffFailureObservation(error){
    const value=object(error)?failures.get(error):null;
    return value?structuredClone(value):null;
}
module.exports={observeSyntheticEntryOwnerContext,entryContextHandoffFailureObservation,EntryContextHandoffRefusal,
    _observeSyntheticEntryOwnerContextWithPhasedCopy:observeSyntheticEntryOwnerContextWithPhasedCopy,
    _observeSyntheticEntryOwnerContextWithComparison:observeSyntheticEntryOwnerContextWithComparison,
    _entryComparisonOwnerFailureObservation:comparisonOwnerFailureObservation};

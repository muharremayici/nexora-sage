'use strict';
// Restricted first-party synthetic source recognition. Input is never executed.
const crypto=require('node:crypto'),{performance}=require('node:perf_hooks'),{types}=require('node:util');
const {_borrowCompilerEntryText:borrow,_disposeCompilerEntry:disposeEntry}=require('./compiler_entry_owner.cjs');
const {_canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate,StreamStagerRefusal}=require('./compiler_stream_stager.cjs');
const {recognize,CompilerExportRecipeRefusal,need,sum}=require('./compiler_source_recipe_cursor.cjs');
const get=Object.getOwnPropertyDescriptor,has=Object.hasOwn,keys=Reflect.ownKeys,freeze=Object.freeze,apply=Reflect.apply;
const PROFILE='private_compiler_export_recipe',GRAMMAR='direct_descriptor_recipe_ascii_v1',SPAN='utf16_half_open_strict_utf8_slice_v1';
const HELPER_PROFILE='private_compiler_helper_site',HELPER_GRAMMAR='literal_helper_site_ascii_v1';
const WINDOW_PROFILE='private_compiler_helper_window',WINDOW_SPAN='view_local_utf16_half_open_strict_utf8_slice_v1';
const active=new WeakMap(),capsules=new WeakMap(),stages=new WeakMap();
const sha=value=>crypto.createHash('sha256').update(value).digest('hex'),hash=value=>sha(canonical(value)),bytes=value=>Buffer.byteLength(canonical(value),'utf8');
const object=v=>v!==null&&(typeof v==='object'||typeof v==='function');
function note(error,stage){if(object(error)&&!stages.has(error))stages.set(error,stage);return error;}
function _compilerExportRecipeFailureStage(error){return object(error)?stages.get(error)||'unknown':'unknown';}
function copy(v,code,seen=new Set()){
    if(v===null||['string','boolean','number'].includes(typeof v))return v;
    need(v&&typeof v==='object'&&!types.isProxy(v)&&!seen.has(v),code);
    need(Array.isArray(v)||Object.getPrototypeOf(v)===Object.prototype||Object.getPrototypeOf(v)===null,code);seen.add(v);
    try{
        const ds=Object.getOwnPropertyDescriptors(v),ks=keys(ds);need(ks.every(k=>typeof k==='string'&&has(ds[k],'value')),code);
        if(Array.isArray(v)){
            const n=ds.length?.value;need(Number.isSafeInteger(n)&&n>=0&&ks.length===n+1,code);
            return Array.from({length:n},(_,i)=>{need(has(ds,String(i)),code);return copy(ds[i].value,code,seen);});
        }
        return Object.fromEntries(ks.map(k=>[k,copy(ds[k].value,code,seen)]));
    }finally{seen.delete(v);}
}
function field(v,k){need(v&&typeof v==='object'&&!types.isProxy(v),'recipe_policy_invalid');const d=get(v,k);need(d&&has(d,'value'),'recipe_policy_invalid');return d.value;}
function checked(v,d,s,code){try{validate(v,d,s,code);}catch(e){if(e instanceof StreamStagerRefusal)throw new CompilerExportRecipeRefusal(e.code===code?code:'recipe_policy_invalid');throw e;}}

function disposeCapsule(token,model){const row=capsules.get(token);if(!row||row.model!==model)return false;row.facts=row.observation=null;row.state='DISPOSED';capsules.delete(token);return true;}
function _disposeCompilerExportRecipeCapsule(token){return disposeCapsule(token,PROFILE);}
function _disposeCompilerHelperSiteCapsule(token){return disposeCapsule(token,HELPER_PROFILE);}
function _disposeCompilerHelperWindowCapsule(token){return disposeCapsule(token,WINDOW_PROFILE);}
function consumeCapsule(token,expected,model){
    const row=capsules.get(token);need(row&&row.model===model,'recipe_order');if(row.state!=='HELD'){row.control.poisoned=true;throw new CompilerExportRecipeRefusal('recipe_order');}row.state='CONSUMING';
    try{
        need(hash(copy(expected,'recipe_expected_invalid'))===row.expectedHash,'recipe_identity');row.check();
        need(row.state==='CONSUMING'&&row.observation,'recipe_order');row.control.stage='consume';row.control.consumptions++;
        const observation=copy(row.observation,'recipe_result_invalid');row.check();
        need(row.state==='CONSUMING'&&row.observation!==null&&capsules.get(token)===row,'recipe_order');return observation;
    }catch(error){row.control.poisoned=true;throw note(error,row.control.stage);}
    finally{disposeCapsule(token,model);}
}
function _consumeCompilerExportRecipeCapsule(token,expected){return consumeCapsule(token,expected,PROFILE);}
function _consumeCompilerHelperSiteCapsule(token,expected){return consumeCapsule(token,expected,HELPER_PROFILE);}
function _consumeCompilerHelperWindowCapsule(token,expected){return consumeCapsule(token,expected,WINDOW_PROFILE);}

function observeSyntheticCompilerExportRecipe(owner,expectedInput,profilesInput,schemasInput,options){
    return observeSyntheticSource(owner,expectedInput,profilesInput,schemasInput,options,false);
}
function observeSyntheticCompilerHelperSite(owner,expectedInput,profilesInput,schemasInput,options){
    return observeSyntheticSource(owner,expectedInput,profilesInput,schemasInput,options,true);
}
function observeSyntheticCompilerHelperWindow(owner,expectedInput,profilesInput,schemasInput,options){
    return observeSyntheticSource(owner,expectedInput,profilesInput,schemasInput,options,'window');
}
function observeSyntheticSource(owner,expectedInput,profilesInput,schemasInput,options,helperSite){
    const windowMode=helperSite==='window';helperSite=!!helperSite;
    const profile=windowMode?WINDOW_PROFILE:helperSite?HELPER_PROFILE:PROFILE,schemaKey=windowMode?'helper_window':helperSite?'helper_site':'recipe';
    const previous=active.get(owner);if(previous){previous.poisoned=true;disposeEntry(owner);throw new CompilerExportRecipeRefusal('recipe_order');}
    const control={stage:'validation',poisoned:false,consumptions:0};let registered=false,inBorrow=false,held=null,observe=null,staged=null,token=null,candidate=null,primary=null,hasPrimary=false;
    let wholeBuffer=null,viewBuffer=null,viewText=null;
    try{
        need(object(owner),'recipe_order');active.set(owner,control);registered=true;
        const now=options?.now||(()=>performance.now()),deadline=options?.deadline;need(typeof now==='function','recipe_deadline');
        const started=now();let last=started;need(Number.isFinite(started)&&Number.isFinite(deadline)&&started<deadline,'recipe_deadline');
        const check=()=>{const time=now();need(!control.poisoned,'recipe_order');need(Number.isFinite(time)&&time>=last&&time<deadline,'recipe_deadline');last=time;
            if(inBorrow&&observe){const s=apply(observe,owner,[]);need(s.state==='BORROWING'&&s.entry_owner_registered&&s.entry_input_verified,'recipe_order');}};
        const d=copy(field(profilesInput,profile),'recipe_policy_invalid'),e=copy(expectedInput,'recipe_expected_invalid'),schemas=copy(schemasInput,'recipe_policy_invalid');
        const entry=copy(field(profilesInput,d.entry_profile),'recipe_policy_invalid'),capture=copy(field(profilesInput,d.capture_profile),'recipe_policy_invalid'),image=copy(field(profilesInput,d.image_profile),'recipe_policy_invalid');
        const schema=schemas[schemaKey];
        need(keys(schemas).sort().join(',')==='entry,'+schemaKey&&schema?.$defs?.policy&&schemas.entry?.$defs?.snapshot&&image?.limits,'recipe_policy_invalid');
        checked(d,schema.$defs.policy,schema,'recipe_policy_invalid');checked(e,schema.$defs.expected,schema,'recipe_expected_invalid');
        need(d.version===(windowMode?'v1-private-synthetic-helper-window':helperSite?'v1-private-synthetic-helper-site':'v1-private-synthetic-export-recipe')
            &&d.model===(windowMode?'restricted_synthetic_exact_interval_helper_window_v1':helperSite?'restricted_synthetic_literal_helper_site_v1':'restricted_synthetic_direct_descriptor_recipe_v1')
            &&d.grammar===(helperSite?HELPER_GRAMMAR:GRAMMAR)&&d.span_scheme===(windowMode?WINDOW_SPAN:SPAN)&&(!helperSite||d.callee==='__export')
            &&d.complete_status===(windowMode?'STATIC_HELPER_WINDOW_OBSERVED_NOT_CALLSITE_ADMISSION':helperSite?'STATIC_HELPER_SITE_OBSERVED_NOT_RUNTIME_ADMISSION':'STATIC_RECIPE_OBSERVED_NOT_RUNTIME_ADMISSION')
            &&d.incomplete_status===(windowMode?'INCOMPLETE_STATIC_HELPER_WINDOW_NOT_CALLSITE_ADMISSION':helperSite?'INCOMPLETE_STATIC_HELPER_SITE_NOT_RUNTIME_ADMISSION':'INCOMPLETE_STATIC_RECIPE_NOT_RUNTIME_ADMISSION')
            &&d.privacy===(windowMode?'synthetic_helper_window_summary_only_v1':helperSite?'synthetic_helper_site_summary_only_v1':'synthetic_summary_only_v1')
            &&d.consumer===(windowMode?'fixed_helper_window_metadata_consumer_v1':helperSite?'fixed_helper_site_metadata_consumer_v1':'fixed_recipe_metadata_consumer_v1')
            &&d.claim_scope===(windowMode?'exact_selected_synthetic_text_window_only':helperSite?'single_supported_synthetic_helper_site_only':'complete_supported_synthetic_text_only')
            &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>d[k]===false)
            &&d.schema_sha256===hash(schema)&&d.entry_policy_sha256===hash(entry)&&d.entry_schema_sha256===hash(schemas.entry)
            &&entry.schema_sha256===d.entry_schema_sha256&&d.capture_policy_sha256===hash(capture)&&d.image_policy_sha256===hash(image)
            &&entry.capture_policy_sha256===d.capture_policy_sha256&&entry.image_policy_sha256===d.image_policy_sha256,'recipe_policy_invalid');
        const pins={observer_policy_sha256:hash(d),observer_schema_sha256:hash(schema),grammar_sha256:hash(d.grammar),span_scheme_sha256:hash(d.span_scheme),
            privacy_sha256:hash(d.privacy),limits_sha256:hash(d.limits),consumer_sha256:hash(d.consumer),claim_scope_sha256:hash(d.claim_scope)};
        if(helperSite)pins.callee_sha256=hash(d.callee);
        if(windowMode){
            need(d.selection_policy.scheme==='utf16_half_open_exact_interval_v1'&&d.selection_policy.mapping==='checked_absolute_utf16_only_v1'
                &&d.selection_policy.context==='unverified_enclosing_source_v1','recipe_policy_invalid');
            Object.assign(pins,{whole_limits_sha256:hash(d.whole_limits),selection_policy_sha256:hash(d.selection_policy),
                selection_schema_sha256:hash(schema.$defs.selection),selection_sha256:hash(e.selection)});
        }
        need(Object.entries(pins).every(([k,v])=>e[k]===v)&&e.entry.policy_sha256===d.entry_policy_sha256&&e.entry.schema_sha256===d.entry_schema_sha256,'recipe_identity');
        const limits=d.limits;need(Object.values(limits).every(n=>Number.isSafeInteger(n)&&n>0&&n<=image.limits.metadata_bytes),'recipe_policy_invalid');
        if(windowMode){
            const w=d.whole_limits;need(Object.values(w).every(n=>Number.isSafeInteger(n)&&n>0),'recipe_policy_invalid');
            need(w.source_code_units<=capture.max_module_bytes&&w.verification_utf8_bytes<=capture.max_module_bytes
                &&w.verification_work_units<=sum(capture.max_module_bytes,capture.max_module_bytes)
                &&w.selection_work_units<=sum(capture.max_module_bytes,capture.max_module_bytes),'recipe_policy_invalid');
        }
        need(deadline<=started+image.limits.capture_ms,'recipe_deadline');
        held={expected:e,descriptor:d,entry,capture,image,schemas};
        const inputBytes=bytes(held);need(inputBytes<=image.limits.metadata_bytes,'recipe_budget');
        let snapshot=null,metadataBytes=0,reserved=0,total=0,sourceUnits=0,observation=null,windowSummary=null;
        const metrics={scan_code_units:0,token_count:0,work_units:0,fact_bytes:0,name_utf8_bytes:0,gap_bytes:0};
        if(windowMode)Object.assign(metrics,{whole_verification_code_units:0,whole_verification_utf8_bytes:0,whole_verification_work_units:0,
            selection_work_units:0,selection_work_reserved_units:0,view_reserved_utf8_bytes:0});
        borrow(owner,e.entry,row=>{
            inBorrow=true;control.stage='entry_identity';check();
            const method=get(owner,'snapshot');need(Object.isFrozen(owner)&&method&&has(method,'value')&&typeof method.value==='function'&&!method.writable&&!method.configurable,'recipe_order');
            observe=method.value;snapshot=apply(observe,owner,[]);checked(snapshot,schemas.entry.$defs.snapshot,schemas.entry,'recipe_identity');
            if(windowMode){
                need(hash(snapshot)===e.owner_snapshot_sha256&&snapshot.state==='BORROWING'&&snapshot.entry_owner_registered&&snapshot.entry_input_verified
                    &&row.raw_sha256===e.entry.entry_sha256&&row.text_sha256===e.entry.text_sha256,'recipe_identity');
                const s=e.selection,w=d.whole_limits;sourceUnits=row.text.length;
                need(s.source_code_units===sourceUnits,'recipe_identity');
                need(Number.isSafeInteger(s.start_utf16)&&Number.isSafeInteger(s.end_utf16)&&s.start_utf16>=0&&s.start_utf16<s.end_utf16&&s.end_utf16<=sourceUnits,'recipe_expected_invalid');
                const viewUnits=s.end_utf16-s.start_utf16,maxViewBytes=sum(viewUnits,viewUnits,viewUnits,viewUnits);
                need(s.view_code_units===viewUnits&&s.view_utf8_bytes>0&&s.view_utf8_bytes<=maxViewBytes,'recipe_identity');
                control.stage='window_budget';
                need(sourceUnits<=w.source_code_units&&row.size_bytes<=w.verification_utf8_bytes&&viewUnits<=limits.scan_code_units
                    &&sum(sourceUnits,row.size_bytes)<=w.verification_work_units
                    &&sum(4,viewUnits,viewUnits,maxViewBytes)<=w.selection_work_units,'recipe_budget');
                metrics.selection_work_reserved_units=sum(4,viewUnits,viewUnits,maxViewBytes);
                metadataBytes=sum(inputBytes,inputBytes,bytes(snapshot));need(metadataBytes<=image.limits.metadata_bytes,'recipe_budget');
                reserved=sum(limits.fact_bytes,limits.fact_bytes,limits.name_utf8_bytes,limits.name_utf8_bytes,limits.gap_bytes,limits.cursor_bytes,
                    row.size_bytes,viewUnits,viewUnits,maxViewBytes);
                total=sum(snapshot.known_owner_input_bytes,sourceUnits,sourceUnits,metadataBytes,reserved,limits.summary_bytes);
                need(total<=image.limits.image_bytes,'recipe_budget');check();control.stage='selection';
                const split=at=>{
                    if(at===0||at===sourceUnits)return false;
                    metrics.selection_work_units=sum(metrics.selection_work_units,2);
                    const first=row.text.charCodeAt(at-1),second=row.text.charCodeAt(at);
                    return first>=0xd800&&first<=0xdbff&&second>=0xdc00&&second<=0xdfff;
                };
                need(!split(s.start_utf16)&&!split(s.end_utf16),'recipe_expected_invalid');check();control.stage='whole_verification';
                wholeBuffer=Buffer.from(row.text,'utf8');
                need(wholeBuffer.length===row.size_bytes&&sha(wholeBuffer)===e.entry.text_sha256,'recipe_identity');
                metrics.whole_verification_code_units=sourceUnits;metrics.whole_verification_utf8_bytes=row.size_bytes;
                metrics.whole_verification_work_units=sum(sourceUnits,row.size_bytes);wholeBuffer=null;check();control.stage='selection';
                viewText=row.text.slice(s.start_utf16,s.end_utf16);viewBuffer=Buffer.from(viewText,'utf8');
                need(viewText.length===viewUnits&&viewBuffer.length===s.view_utf8_bytes&&sha(viewBuffer)===s.view_text_sha256,'recipe_identity');
                metrics.selection_work_units=sum(metrics.selection_work_units,viewUnits,viewUnits,viewBuffer.length);
                metrics.view_reserved_utf8_bytes=maxViewBytes;viewBuffer=null;check();control.stage='scan';
                staged=recognize(viewText,limits,check,metrics,true,{origin:s.start_utf16,end:s.end_utf16,context:e.entry.context_sha256,selection:e.selection_sha256});
                windowSummary={start_utf16:s.start_utf16,end_utf16:s.end_utf16,view_code_units:viewUnits,view_utf8_bytes:s.view_utf8_bytes,
                    view_text_sha256:s.view_text_sha256,selection_sha256:e.selection_sha256,provenance_sha256:s.provenance_sha256,
                    global_start_utf8:null,global_end_utf8:null,global_line:null,global_column:null};viewText=null;
            }else{
                need(hash(snapshot)===e.owner_snapshot_sha256&&snapshot.state==='BORROWING'&&snapshot.entry_owner_registered&&snapshot.entry_input_verified
                    &&row.raw_sha256===e.entry.entry_sha256&&row.text_sha256===e.entry.text_sha256&&sha(row.text)===e.entry.text_sha256,'recipe_identity');
                sourceUnits=row.text.length;need(sourceUnits<=limits.scan_code_units&&Buffer.byteLength(row.text,'utf8')===row.size_bytes,'recipe_budget');
                metadataBytes=sum(inputBytes,inputBytes,bytes(snapshot));need(metadataBytes<=image.limits.metadata_bytes,'recipe_budget');
                reserved=sum(limits.fact_bytes,limits.fact_bytes,limits.name_utf8_bytes,limits.name_utf8_bytes,limits.gap_bytes,limits.cursor_bytes,row.size_bytes);
                total=sum(snapshot.known_owner_input_bytes,sourceUnits,sourceUnits,metadataBytes,reserved,limits.summary_bytes);
                need(total<=image.limits.image_bytes,'recipe_budget');check();control.stage='scan';
                staged=recognize(row.text,limits,check,metrics,helperSite);
            }
            check();
            if(staged.facts)checked(staged.facts,schema.$defs.facts,schema,'recipe_result_invalid');
            control.stage='projection';need(hash(staged.observation)===e.observation_sha256,'recipe_identity');check();
            // Deliberately no callback result: the entry owner cannot inspect a .then.
        });
        inBorrow=false;control.stage='settlement';check();const after=apply(observe,owner,[]);
        need(after.state==='DISPOSED'&&!after.entry_owner_registered&&!after.entry_input_verified&&after.retained_entry_bytes===0&&after.retained_text_code_units===0,'recipe_order');
        token=freeze({kind:windowMode?'private_synthetic_helper_window_capsule':helperSite?'private_synthetic_helper_site_capsule':'private_synthetic_export_recipe_capsule'});
        capsules.set(token,{model:profile,state:'HELD',facts:staged.facts,observation:staged.observation,expectedHash:hash(e),control,check});staged=null;
        control.stage='capsule_ready';check();observation=consumeCapsule(token,e,profile);
        need(!capsules.has(token)&&control.consumptions===1,'recipe_order');control.stage='result';check();
        candidate={version:d.version,status:(windowMode?observation.view_syntax_complete:helperSite?observation.complete_site_syntax_within_grammar:observation.complete_recipe_within_grammar)?d.complete_status:d.incomplete_status,entry:{...e.entry},expected_sha256:hash(e),...pins,
            owner_snapshot_sha256:e.owner_snapshot_sha256,observation_sha256:e.observation_sha256,observation,...metrics,entry_capture_read_bytes:snapshot.capture_read_bytes,
            entry_known_input_bytes:snapshot.known_owner_input_bytes,source_text_code_units:sourceUnits,source_text_reserved_bytes:sum(sourceUnits,sourceUnits),
            observer_metadata_bytes:metadataBytes,observer_reserved_bytes:reserved,summary_reserved_bytes:limits.summary_bytes,known_inputs_reserved_bytes:total,
            entry_borrows:1,capsule_consumptions:1,entry_owner_disposed:true,capsule_owner_disposed:true,local_source_refs_cleared:true,
            ...(windowMode?{synthetic_helper_window_observed:true,window:windowSummary,owned_source_units:sourceUnits,scanned_units:metrics.scan_code_units,
                whole_file_coverage:false,helper_identity_resolved:false,target_binding_resolved:false,enclosing_source_syntax_verified:false,
                actual_callsite_context_verified:false,global_byte_line_mapping_verified:false}:helperSite?{synthetic_helper_site_observed:true,owned_source_units:sourceUnits,scanned_units:metrics.scan_code_units,
                whole_file_coverage:false,helper_identity_resolved:false,target_binding_resolved:false}:{synthetic_source_recipe_observed:true}),
            ...Object.fromEntries(['actual_export_set_complete','runtime_descriptor_observed','runtime_prototype_observed','runtime_integrity_observed','compiler_loaded','loaded_code_attested',
                'native_execution_attested','source_freshness_verified','snapshot_bound','image_admitted','production_enabled','semantic_lineage','cache_eligible','publication_authority'].map(k=>[k,false])),
            entry_evaluations:0,getter_invocations:0,api_invocations:0,backend_invocations:0,compiled_heap_bytes:null,runtime_heap_bytes:null,peak_rss_bytes:null};
        checked(candidate,schema.$defs.result,schema,'recipe_result_invalid');need(bytes(candidate)<=limits.summary_bytes,'recipe_budget');
        held=observe=null;freeze(candidate.entry);if(windowMode)freeze(candidate.window);freeze(candidate.observation.gap);freeze(candidate.observation);freeze(candidate);check();
    }catch(error){primary=note(error,control.stage);hasPrimary=true;candidate=null;}
    finally{
        const cleanup=[];try{disposeCapsule(token,profile);}catch(e){cleanup.push(note(e,'cleanup'));}
        try{disposeEntry(owner);}catch(e){cleanup.push(note(e,'cleanup'));}
        held=observe=staged=token=wholeBuffer=viewBuffer=viewText=null;if(registered)active.delete(owner);
        if(cleanup.length){primary=note(new AggregateError(hasPrimary?[primary,...cleanup]:cleanup,'compiler_export_recipe_cleanup_failed',hasPrimary?{cause:primary}:undefined),'cleanup');hasPrimary=true;}
    }
    if(hasPrimary)throw primary;return candidate;
}

module.exports={observeSyntheticCompilerExportRecipe,observeSyntheticCompilerHelperSite,observeSyntheticCompilerHelperWindow,
    _consumeCompilerExportRecipeCapsule,_disposeCompilerExportRecipeCapsule,_consumeCompilerHelperSiteCapsule,_disposeCompilerHelperSiteCapsule,
    _consumeCompilerHelperWindowCapsule,_disposeCompilerHelperWindowCapsule,
    _compilerExportRecipeFailureStage,CompilerExportRecipeRefusal};

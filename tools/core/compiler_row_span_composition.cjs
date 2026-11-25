'use strict';
// Private supplied-synthetic same-process composition. No file, transport, owner or default entrypoint.
const crypto=require('node:crypto'),{performance}=require('node:perf_hooks'),{types}=require('node:util');
const locatorAPI=require('./compiler_row_locator.cjs'),spanAPI=require('./compiler_span_selection.cjs'),associationAPI=require('./compiler_row_span_association.cjs');
const {copy,CompilerTextWindowRefusal}=require('./compiler_text_window.cjs');
const {_canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate,StreamStagerRefusal}=require('./compiler_stream_stager.cjs');
const PROFILE='private_compiler_row_span_composition',failures=new WeakMap();
class CompilerRowSpanCompositionRefusal extends Error{constructor(code){super(code);this.code=code;}}
const need=(v,c)=>{if(!v)throw new CompilerRowSpanCompositionRefusal(c);};
const size=t=>Buffer.byteLength(t,'utf8'),sha=v=>crypto.createHash('sha256').update(v).digest('hex');
function field(v,k){need(v&&typeof v==='object'&&!types.isProxy(v),'composition_policy_invalid');const p=Object.getOwnPropertyDescriptor(v,k);need(p&&Object.hasOwn(p,'value'),'composition_policy_invalid');return p.value;}
function sum(...values){const n=values.reduce((a,b)=>a+b,0);need(values.every(v=>Number.isSafeInteger(v)&&v>=0)&&Number.isSafeInteger(n),'composition_budget');return n;}
function checked(v,k,s,c){try{validate(v,s.$defs[k],s,c);}catch(e){if(e instanceof StreamStagerRefusal)throw new CompilerRowSpanCompositionRefusal(c);throw e;}}
function supported(s){
    const keys=['$schema','$id','title','$defs','$ref','type','required','properties','additionalProperties','const','enum','items','minItems','minLength','minimum','pattern'];
    need(s&&typeof s==='object'&&!Array.isArray(s)&&Object.keys(s).every(k=>keys.includes(k)),'composition_policy_invalid');
    for(const map of [s.$defs,s.properties])if(map)for(const child of Object.values(map))supported(child);if(s.items)supported(s.items);
}
const disabled=v=>v&&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>v[k]===false);
const selectionOf=v=>Object.fromEntries(['start_utf16','end_utf16','start_utf8','end_utf8','view_utf16_units','view_utf8_bytes','view_text_sha256'].map(k=>[k,v[k]]));
function composeSuppliedSyntheticRowSpan(text,expectedInput,profilesInput,schemasInput,options){
    let context=null,locator=null,locatorText=null,spanRaw=null,spanText=null,associationRaw=null,associationText=null,association=null,receipt=null,wire=null,compositionRaw=null,bundle=null;
    let stage='start',primary,failed=false,publishing=false,terminal=0,workReservation=null,nativeStage='unknown';
    const calls={locator:0,span:0,association:0},returns={locator:0,span:0,association:0},known={locator:null,span:null,association:null};let work=0;
    try{
        const now=options?.now||(()=>performance.now());need(typeof now==='function','composition_deadline');
        const started=now();let last=started,deadline=options?.deadline;need(Number.isFinite(started),'composition_deadline');stage='policy';
        const imageInput=field(profilesInput,'private_input_image_experiment'),ceiling=field(field(imageInput,'limits'),'metadata_bytes');
        need(Number.isSafeInteger(ceiling)&&ceiling>0,'composition_policy_invalid');
        context=copy({descriptor:field(profilesInput,PROFILE),profiles:Object.fromEntries(['private_compiler_row_locator','private_compiler_span_selection','private_compiler_row_span_association','private_compiler_row_fragment','private_input_image_experiment'].map(k=>[k,field(profilesInput,k)])),
            schemas:schemasInput,expected:expectedInput},'composition_metadata_invalid',ceiling);
        const {descriptor:d,profiles:p,schemas:s,expected:e}=context;need(d&&d.limits&&typeof d.limits==='object','composition_policy_invalid');const l=d.limits;
        need(Object.values(l).every(v=>Number.isSafeInteger(v)&&v>0),'composition_policy_invalid');
        const charge=n=>{work=sum(work,n);need(sum(work,publishing?0:terminal)<=l.parent_byte_work_units
            &&(workReservation===null||sum(work,publishing?0:terminal)<=workReservation),'composition_budget');};
        const canon=v=>{const t=canonical(v);charge(size(t));return t;};
        const bytes=v=>size(canon(v));
        const hash=v=>{const t=canon(v);charge(size(t));return sha(t);};
        const hashWire=v=>{charge(typeof v==='string'?size(v):v.length);return sha(v);};
        const fixedBytes=size(canonical(context));charge(2*fixedBytes);need(10*fixedBytes<=l.parent_metadata_bytes,'composition_budget');
        need(s&&typeof s==='object'&&Object.keys(s).sort().join(',')==='association,composition,locator,span','composition_policy_invalid');
        for(const schema of Object.values(s))supported(schema);
        need(['policy','expected','candidate','receipt','failure','source','selection','coverage','flags'].every(k=>s.composition.$defs?.[k]),'composition_policy_invalid');
        const lp=p.private_compiler_row_locator,sp=p.private_compiler_span_selection,ap=p.private_compiler_row_span_association,row=p.private_compiler_row_fragment,image=p.private_input_image_experiment;
        checked(d,'policy',s.composition,'composition_policy_invalid');checked(e,'expected',s.composition,'composition_expected_invalid');
        checked(lp,'policy',s.locator,'composition_policy_invalid');checked(sp,'policy',s.span,'composition_policy_invalid');checked(ap,'policy',s.association,'composition_policy_invalid');
        checked(e.candidate,'candidate',s.locator,'composition_expected_invalid');
        need(d.version==='v1-private-supplied-row-span-composition'&&d.model==='supplied_synthetic_once_per_stage_row_span_composition_v1'
            &&d.budget_model==='sum_reference_child_graphs_parent_templates_and_codecs_v1'&&d.input_kind==='same_process_builtin_supplied_string_v1'
            &&d.method==='reviewed_once_per_stage_supplied_row_span_v1'&&d.purpose==='compose_one_supplied_synthetic_row_span'
            &&d.deadline_policy==='one_original_monotonic_parent_deadline_v1'&&d.privacy==='selected_metadata_and_bounded_stage_facts_only_v1'
            &&d.result_status==='SUPPLIED_SYNTHETIC_ROW_SPAN_COMPOSITION_RECORDED_NOT_ORIGIN_ATTESTATION'
            &&[d,lp,sp,ap,row].every(disabled),'composition_policy_invalid');
        need(lp.version==='v1-private-supplied-row-candidate-locator'&&lp.grammar==='finite_ascii_single_arrow_occurrences_v1'
            &&sp.version==='v1-private-supplied-exact-span-selection'&&sp.method==='reviewed_exact_utf16_span_v1'
            &&ap.version==='v1-private-supplied-row-span-association'&&ap.model==='supplied_canonical_metadata_row_span_consistency_v1'
            &&row.version==='v1-private-supplied-local-row-fragment'&&row.grammar==='literal_arrow_rows_ascii_v1','composition_policy_invalid');
        const id={policy:hash(d),schema:hash(s.composition),locator:hash(lp),locator_schema:hash(s.locator),span:hash(sp),span_schema:hash(s.span),
            association:hash(ap),association_schema:hash(s.association),row:hash(row),image:hash(image),expected:hash(e)};
        need(d.schema_sha256===id.schema&&d.locator_policy_sha256===id.locator&&d.span_policy_sha256===id.span&&d.association_policy_sha256===id.association
            &&d.row_policy_sha256===id.row&&d.image_policy_sha256===id.image&&lp.schema_sha256===id.locator_schema&&sp.schema_sha256===id.span_schema&&ap.schema_sha256===id.association_schema
            &&lp.row_grammar_policy_sha256===id.row&&sp.reference_policy_sha256===id.row&&ap.row_policy_sha256===id.row
            &&[lp,sp,ap].every(v=>v.image_policy_sha256===id.image)&&ap.locator_policy_sha256===id.locator&&ap.span_policy_sha256===id.span,'composition_policy_invalid');
        need(hash(s.composition.$defs.candidate)===hash(s.locator.$defs.candidate)
            &&['source','selection','coverage','flags'].every(k=>hash(s.composition.$defs[k])===hash(s.association.$defs[k]))
            &&Object.values(s.composition.$defs.flags.properties).every(v=>v.const===false),'composition_policy_invalid');
        const ll=lp.limits,lr=lp.resource_limits,sl=sp.limits,al=ap.limits;
        const refs={source_utf16_units:Math.min(ll.scan_code_units,sl.source_utf16_units),parent_metadata_bytes:Math.min(row.resource_limits.metadata_bytes,image.limits.metadata_bytes),
            locator_snapshot_bytes:ll.summary_bytes,span_receipt_bytes:sl.receipt_bytes,association_receipt_bytes:al.output_bytes,
            composition_receipt_bytes:Math.min(ll.summary_bytes,sl.receipt_bytes,al.output_bytes),parent_byte_work_units:Math.min(row.resource_limits.metadata_bytes,image.limits.metadata_bytes),
            image_bytes:Math.min(lr.image_bytes,sl.image_bytes,al.image_bytes,image.limits.image_bytes),operation_ms:Math.min(lr.operation_ms,sl.operation_ms,al.operation_ms,image.limits.capture_ms)};
        need(Object.keys(l).sort().join(',')===Object.keys(refs).sort().join(',')&&Object.keys(refs).every(k=>Number.isSafeInteger(refs[k])&&refs[k]>0&&l[k]<=refs[k]),'composition_policy_invalid');
        terminal=2*refs.composition_receipt_bytes;charge(0);
        if(deadline===undefined)deadline=started+l.operation_ms;
        need(Number.isFinite(deadline)&&started<deadline&&deadline<=started+l.operation_ms,'composition_deadline');
        const check=()=>{const t=now();need(Number.isFinite(t)&&t>=last&&t<deadline,'composition_deadline');last=t;};check();
        need(e.policy_sha256===id.policy&&e.schema_sha256===id.schema&&e.purpose===d.purpose,'composition_identity');
        stage='preflight';need(typeof text==='string','composition_input_invalid');need(text.length<=l.source_utf16_units,'composition_budget');
        const source=e.source,candidate=e.candidate,selected=selectionOf(candidate),U=source.source_utf16_units,B=source.source_utf8_bytes;
        need(U===text.length&&B>=U&&B<=4*U&&B<=Math.min(lr.source_utf8_bytes,sl.source_utf8_bytes)
            &&candidate.start_utf16<candidate.end_utf16&&candidate.end_utf16<=U&&candidate.start_utf8<candidate.end_utf8&&candidate.end_utf8<=B
            &&candidate.view_utf16_units===candidate.end_utf16-candidate.start_utf16&&candidate.view_utf8_bytes===candidate.end_utf8-candidate.start_utf8
            &&candidate.single_row_grammar_policy_sha256===id.row,'composition_expected_invalid');
        const pos={u:ll.scan_code_units,b:lr.source_utf8_bytes,line:ll.scan_code_units+1,column:ll.scan_code_units};
        const tokenTemplate={kind:'string',value:'',start:pos,end:pos},token=8*bytes(tokenTemplate);
        const graphs={locator:sum(2*lr.source_utf8_bytes,4*ll.scan_code_units,lr.metadata_bytes,4*ll.cursor_bytes,2*token,5*ll.fact_bytes,5*ll.gap_bytes,5*ll.summary_bytes),
            span:sum(2*sl.source_utf8_bytes,4*sl.source_utf16_units,sl.metadata_bytes,2*sl.cursor_bytes,10*sl.receipt_bytes),
            association:sum(10*(al.locator_bytes+al.span_receipt_bytes),al.metadata_bytes,10*al.output_bytes)};
        const codecRef=10*sum(refs.locator_snapshot_bytes,refs.span_receipt_bytes,refs.association_receipt_bytes,refs.composition_receipt_bytes),sourceRef=2*refs.source_utf16_units;
        const joint=sum(...Object.values(graphs),refs.parent_metadata_bytes,codecRef,sourceRef);need(joint<=l.image_bytes,'composition_budget');
        const candidateHash=hash(candidate),slot='0'.repeat(64);
        const le={policy_sha256:id.locator,schema_sha256:id.locator_schema,source_text_sha256:source.source_text_utf8_sha256,
            source_utf16_units:U,source_utf8_bytes:B,invocation_id:e.invocations.locator},leHash=hash(le);
        const decisionTemplate={version:ap.decision_version,purpose:ap.decision_purpose,locator_result_sha256:slot,candidate_sha256:candidateHash,
            occurrence_index:candidate.occurrence_index,source,selection:selected,origin_ref_sha256:e.origin_ref_sha256,span_invocation_id:e.invocations.span,
            span_version:sp.version,span_policy_sha256:id.span,span_schema_sha256:id.span_schema};
        const seTemplate={policy_sha256:id.span,schema_sha256:id.span_schema,...source,origin_ref_sha256:e.origin_ref_sha256,selection_ref_sha256:slot,
            start_utf16:candidate.start_utf16,end_utf16:candidate.end_utf16,invocation_id:e.invocations.span,purpose:sp.purpose};
        const aeTemplate={policy_sha256:id.association,schema_sha256:id.association_schema,locator_result_sha256:slot,span_receipt_sha256:slot,
            locator_expected_sha256:leHash,span_expected_sha256:slot,decision_sha256:slot,candidate_sha256:candidateHash,
            locator_policy_sha256:id.locator,locator_schema_sha256:id.locator_schema,span_policy_sha256:id.span,span_schema_sha256:id.span_schema,
            origin_ref_sha256:e.origin_ref_sha256,selection_ref_sha256:slot,occurrence_index:candidate.occurrence_index,source,selection:selected,invocation_id:e.invocations.association,purpose:ap.purpose};
        const maximal=(schema,root,depth=0)=>{
            need(depth<=32,'composition_policy_invalid');
            if(schema.$ref){need(schema.$ref.startsWith('#/$defs/')&&Object.hasOwn(root.$defs,schema.$ref.slice(8)),'composition_policy_invalid');return maximal(root.$defs[schema.$ref.slice(8)],root,depth+1);}
            if(Object.hasOwn(schema,'const'))return schema.const;
            if(schema.enum){let chosen=null,n=-1;for(const value of schema.enum){const b=bytes(value);if(b>n){chosen=value;n=b;}}return chosen;}
            const kinds=Array.isArray(schema.type)?schema.type:[schema.type];
            if(kinds.includes('object'))return Object.fromEntries(Object.entries(schema.properties).map(([k,v])=>[k,maximal(v,root,depth+1)]));
            if(kinds.includes('integer'))return Number.MAX_SAFE_INTEGER;if(kinds.includes('boolean'))return false;
            if(kinds.includes('string')&&schema.pattern==='^[a-f0-9]{64}$')return slot;
            if(kinds.length===1&&kinds[0]==='null')return null;throw new CompilerRowSpanCompositionRefusal('composition_policy_invalid');
        };
        const receiptTemplate=maximal(s.composition.$defs.receipt,s.composition),failureTemplate=maximal(s.composition.$defs.failure,s.composition);
        const receiptTemplateBytes=bytes(receiptTemplate);need(receiptTemplateBytes<=l.composition_receipt_bytes,'composition_budget');
        const templates=[tokenTemplate,le,decisionTemplate,seTemplate,aeTemplate,{expected:aeTemplate,decision:decisionTemplate,locator_expected:le,span_expected:seTemplate},failureTemplate];
        const dynamicBytes=sum(receiptTemplateBytes,...templates.map(bytes)),metadata=10*sum(fixedBytes,dynamicBytes);
        need(metadata<=l.parent_metadata_bytes,'composition_budget');workReservation=sum(metadata,codecRef);need(workReservation<=l.parent_byte_work_units,'composition_budget');charge(0);check();
        stage='locator_call';calls.locator++;locator=locatorAPI.locateSuppliedCompilerRowCandidates(text,le,p,s.locator,{now,deadline});returns.locator++;check();
        stage='locator_validate';need(locator&&locator.producer_policy_sha256===id.locator&&locator.producer_schema_sha256===id.locator_schema&&locator.expected_sha256===leHash
            &&locator.source_text_sha256===source.source_text_utf8_sha256&&locator.source_utf16_units===U&&locator.source_utf8_bytes===B,'composition_locator_invalid');
        const observation=locator.observation,item=observation?.candidates?.[candidate.occurrence_index];
        need(item&&observation.candidate_count===observation.candidates.length&&item.end_utf16<=observation.certified_prefix_end_utf16&&item.end_utf8<=observation.certified_prefix_end_utf8
            &&(observation.scan_complete_within_model?locator.status===lp.complete_status&&observation.gap===null&&observation.unknown_tail_candidate_count===0
                :locator.status===lp.incomplete_status&&observation.gap!==null&&observation.unknown_tail_candidate_count===null)
            &&hash(item)===candidateHash,'composition_selection_invalid');
        locatorText=canon(locator);const locatorBytes=size(locatorText);need(locatorBytes<=l.locator_snapshot_bytes,'composition_budget');known.locator=hashWire(locatorText);check();
        stage='decision';const decision={...decisionTemplate,locator_result_sha256:known.locator},decisionHash=hash(decision);
        const se={...seTemplate,selection_ref_sha256:decisionHash},seHash=hash(se);check();
        const decode=(raw,cap)=>{
            need(Buffer.isBuffer(raw)&&!types.isProxy(raw),'composition_codec_invalid');need(raw.length<=cap,'composition_budget');
            charge(raw.length);const value=raw.toString('utf8');charge(raw.length);const roundtrip=Buffer.from(value,'utf8');charge(raw.length);
            need(roundtrip.equals(raw),'composition_codec_invalid');return value;
        };
        stage='span_call';calls.span++;spanRaw=spanAPI.produceSuppliedExactSpanSelection(text,se,p,s.span,{now,deadline});returns.span++;check();
        stage='span_codec';spanText=decode(spanRaw,l.span_receipt_bytes);const spanBytes=spanRaw.length;known.span=hashWire(spanRaw);check();
        const ae={...aeTemplate,locator_result_sha256:known.locator,span_receipt_sha256:known.span,span_expected_sha256:seHash,decision_sha256:decisionHash,selection_ref_sha256:decisionHash},aeHash=hash(ae);
        stage='association_call';calls.association++;associationRaw=associationAPI.associateSuppliedRowSpanMetadata(locatorText,spanText,{expected:ae,decision,locator_expected:le,span_expected:se},p,
            {association:s.association,locator:s.locator,span:s.span},{now,deadline});returns.association++;check();
        stage='association_codec';associationText=decode(associationRaw,l.association_receipt_bytes);const associationBytes=associationRaw.length;
        known.association=hashWire(associationRaw);charge(associationBytes);
        try{association=JSON.parse(associationText);}catch(error){if(error instanceof SyntaxError)throw new CompilerRowSpanCompositionRefusal('composition_codec_invalid');throw error;}
        charge(associationBytes);association=copy(association,'composition_codec_invalid',l.parent_metadata_bytes);
        need(canon(association)===associationText,'composition_codec_invalid');checked(association,'result',s.association,'composition_association_invalid');
        need(association.producer_policy_sha256===id.association&&association.producer_schema_sha256===id.association_schema&&association.expected_sha256===aeHash
            &&association.locator_result_sha256===known.locator&&association.span_receipt_sha256===known.span&&association.decision_sha256===decisionHash
            &&association.candidate_sha256===candidateHash&&association.occurrence_index===candidate.occurrence_index
            &&Object.keys(source).every(k=>association.source[k]===source[k])&&Object.keys(selected).every(k=>association.selection[k]===selected[k])
            &&association.coverage.observation_sha256===locator.observation_sha256&&association.coverage.locator_status===locator.status
            &&['scan_complete_within_model','candidate_count','certified_prefix_end_utf16','certified_prefix_end_utf8','examined_through_utf16','examined_through_utf8','unscanned_tail_code_units','unknown_tail_candidate_count'].every(k=>association.coverage[k]===observation[k])
            &&association.coverage.gap_sha256===(observation.gap===null?null:hash(observation.gap))&&association.coverage.gap_code===(observation.gap===null?null:observation.gap.code)
            &&association.cost.locator_input_utf8_bytes===locatorBytes&&association.cost.span_input_utf8_bytes===spanBytes
            &&association.cost.input_bytes_hashed===sum(locatorBytes,spanBytes)&&association.cost.checked_candidate_count===observation.candidate_count
            &&association.cost.terminal_byte_work_reserved_units===2*al.output_bytes
            &&sum(association.cost.prepublication_metadata_byte_work_units,association.cost.terminal_byte_work_reserved_units)<=al.metadata_byte_work_units
            &&Object.values(association.flags).every(v=>v===false),'composition_association_invalid');check();
        const cost={child_locator_reserved_bytes:graphs.locator,child_span_reserved_bytes:graphs.span,child_association_reserved_bytes:graphs.association,
            parent_fixed_metadata_bytes:fixedBytes,parent_dynamic_template_bytes:dynamicBytes,parent_metadata_reserved_bytes:metadata,
            parent_metadata_reference_reserved_bytes:refs.parent_metadata_bytes,parent_codec_reference_reserved_bytes:codecRef,parent_source_reference_reserved_bytes:sourceRef,joint_reserved_bytes:joint,
            locator_snapshot_bytes:locatorBytes,span_receipt_bytes:spanBytes,association_receipt_bytes:associationBytes,
            locator_work_units_observed:locator.work_units,locator_source_identity_bytes_observed:locator.source_identity_bytes_hashed,locator_candidate_identity_bytes_observed:locator.candidate_identity_bytes_hashed,
            span_source_identity_bytes_expected:B,span_prefix_work_units_expected:sum(candidate.end_utf16,candidate.end_utf8),span_native_counters_observed:false,
            association_prepublication_byte_work_observed:association.cost.prepublication_metadata_byte_work_units,
            association_terminal_byte_work_reserved:association.cost.terminal_byte_work_reserved_units,association_terminal_byte_work_derived:2*associationBytes,
            parent_prepublication_byte_work_units:work,parent_terminal_byte_work_reserved_units:terminal,parent_byte_work_reserved_units:workReservation};
        receipt={version:d.version,status:d.result_status,method:d.method,purpose:d.purpose,deadline_policy:d.deadline_policy,producer_policy_sha256:id.policy,producer_schema_sha256:id.schema,
            expected_sha256:id.expected,locator_snapshot_sha256:known.locator,span_receipt_sha256:known.span,association_receipt_sha256:known.association,
            locator_expected_sha256:leHash,span_expected_sha256:seHash,association_expected_sha256:aeHash,decision_sha256:decisionHash,candidate_sha256:candidateHash,origin_ref_sha256:e.origin_ref_sha256,
            source:{...source},selection:{...selected},coverage:{...association.coverage},calls:{...calls},returns:{...returns},cost,
            flags:Object.fromEntries(Object.keys(s.composition.$defs.flags.properties).map(k=>[k,false])),raw_source_retained:false,private_refs_cleared:true};
        need(Object.values(calls).every(n=>n===1)&&Object.values(returns).every(n=>n===1),'composition_stage_invalid');
        checked(receipt,'receipt',s.composition,'composition_result_invalid');stage='settlement';text=locator=locatorText=spanRaw=spanText=associationText=association=context=null;check();
        stage='publish';for(const value of [receipt.source,receipt.selection,receipt.coverage,receipt.calls,receipt.returns,cost,receipt.flags,receipt])Object.freeze(value);check();
        publishing=true;wire=canon(receipt);need(size(wire)<=l.composition_receipt_bytes,'composition_budget');charge(size(wire));compositionRaw=Buffer.from(wire,'utf8');check();
        bundle=Object.freeze({association_receipt:associationRaw,composition_receipt:compositionRaw});check();
    }catch(error){
        primary=error instanceof CompilerTextWindowRefusal?Object.assign(new CompilerRowSpanCompositionRefusal(error.code),{cause:error}):error;failed=true;
        const api=stage==='locator_call'?locatorAPI:stage==='span_call'?spanAPI:stage==='association_call'?associationAPI:null;
        if(api){const observed=api.failureStage(primary);if(['start','policy','input','scan','mapping','coverage','selection','settlement','publish','documents','unknown'].includes(observed))nativeStage=observed;}
    }finally{text=context=locator=locatorText=spanRaw=spanText=associationText=association=receipt=wire=null;if(failed)associationRaw=compositionRaw=bundle=null;}
    if(failed){if(primary&&['object','function'].includes(typeof primary))failures.set(primary,{stage,native_stage:nativeStage,calls:{...calls},returns:{...returns},known_sha256:{...known},parent_byte_work_units:work,receipt_emitted:false,source_cleared:true});throw primary;}
    return bundle;
}
function failureObservation(error){const v=error&&['object','function'].includes(typeof error)?failures.get(error):null;return v?{...v,calls:{...v.calls},returns:{...v.returns},known_sha256:{...v.known_sha256}}:null;}
function failureStage(error){return failureObservation(error)?.stage||'unknown';}
module.exports={composeSuppliedSyntheticRowSpan,CompilerRowSpanCompositionRefusal,failureObservation,failureStage};

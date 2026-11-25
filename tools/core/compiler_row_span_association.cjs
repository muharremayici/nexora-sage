'use strict';
// Isolated supplied-metadata consistency only: no source, producer, owner or default caller.
const crypto=require('node:crypto'),{performance}=require('node:perf_hooks'),{types}=require('node:util');
const {copy,CompilerTextWindowRefusal}=require('./compiler_text_window.cjs');
const {_canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate,StreamStagerRefusal}=require('./compiler_stream_stager.cjs');
const PROFILE='private_compiler_row_span_association',failures=new WeakMap();
class CompilerRowSpanAssociationRefusal extends Error{constructor(code){super(code);this.code=code;}}
const need=(v,c)=>{if(!v)throw new CompilerRowSpanAssociationRefusal(c);};
function field(v,k){need(v&&typeof v==='object'&&!types.isProxy(v),'association_policy_invalid');const p=Object.getOwnPropertyDescriptor(v,k);need(p&&Object.hasOwn(p,'value'),'association_policy_invalid');return p.value;}
function checked(v,k,s,c){try{validate(v,s.$defs[k],s,c);}catch(e){if(e instanceof StreamStagerRefusal)throw new CompilerRowSpanAssociationRefusal(c);throw e;}}
function supported(s){
    const allowed=['$schema','$id','title','$defs','$ref','type','required','properties','additionalProperties','const','enum','items','minItems','minLength','minimum','pattern'];
    need(s&&typeof s==='object'&&!Array.isArray(s)&&Object.keys(s).every(k=>allowed.includes(k)),'association_policy_invalid');
    for(const map of [s.$defs,s.properties])if(map)for(const child of Object.values(map))supported(child);
    if(s.items)supported(s.items);
}
function sum(...v){const n=v.reduce((a,b)=>a+b,0);need(v.every(x=>Number.isSafeInteger(x)&&x>=0)&&Number.isSafeInteger(n),'association_budget');return n;}
const byteLength=v=>Buffer.byteLength(v,'utf8');
const sha=v=>crypto.createHash('sha256').update(v).digest('hex');
const sourceOf=v=>({source_text_utf8_sha256:v.source_text_utf8_sha256,source_utf16_units:v.source_utf16_units,source_utf8_bytes:v.source_utf8_bytes});
const selectionOf=v=>Object.fromEntries(['start_utf16','end_utf16','start_utf8','end_utf8','view_utf16_units','view_utf8_bytes','view_text_sha256'].map(k=>[k,v[k]]));
const equal=(a,b,keys)=>keys.every(k=>a[k]===b[k]);
const sourceEqual=(a,b)=>equal(a,b,['source_text_utf8_sha256','source_utf16_units','source_utf8_bytes']);
const selectionEqual=(a,b)=>equal(a,b,['start_utf16','end_utf16','start_utf8','end_utf8','view_utf16_units','view_utf8_bytes','view_text_sha256']);
const disabled=v=>['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>v[k]===false);
const denied=()=>Object.fromEntries(['producer_execution_authenticated','origin_authenticated','locator_origin_attested','selection_intent_authenticated','same_read_attested',
    'source_freshness_verified','physical_path_verified','global_byte_line_mapping_verified','read_authority_granted','filesystem_capture_attested',
    'controller_request_admitted','owner_request_admitted','actual_grammar_replayed','table_membership_verified','table_complete','binding_resolved',
    'actual_export_set_complete','input_code_evaluated','compiler_loaded','runtime_verified','production_enabled','semantic_lineage','cache_eligible','publication_authority'].map(k=>[k,false]));
function associateSuppliedRowSpanMetadata(locatorText,spanText,requestInput,profilesInput,schemasInput,options){
    let context=null,locator=null,spanReceipt=null,result=null,wire=null,output=null,stage='start',primary,failed=false,publishing=false;
    const incurred={input_bytes_hashed:0,metadata_byte_work_units:0,checked_candidate_count:0};
    try{
        const now=options?.now||(()=>performance.now());need(typeof now==='function','association_deadline');
        const started=now();let last=started,deadline=options?.deadline;need(Number.isFinite(started),'association_deadline');
        const imageInput=field(profilesInput,'private_input_image_experiment'),ceiling=field(field(imageInput,'limits'),'metadata_bytes');
        need(Number.isSafeInteger(ceiling)&&ceiling>0,'association_policy_invalid');stage='policy';
        context=copy({descriptor:field(profilesInput,PROFILE),locator:field(profilesInput,'private_compiler_row_locator'),
            span:field(profilesInput,'private_compiler_span_selection'),row:field(profilesInput,'private_compiler_row_fragment'),image:imageInput,
            schemas:schemasInput,request:requestInput},'association_metadata_invalid',ceiling);
        const {descriptor:d,locator:lp,span:sp,row,image,schemas:s,request:q}=context;
        need(d&&typeof d==='object'&&!Array.isArray(d),'association_policy_invalid');const l=d.limits;
        need(l&&Object.values(l).every(n=>Number.isSafeInteger(n)&&n>0),'association_policy_invalid');
        const terminal=2*l.output_bytes;
        const charge=n=>{incurred.metadata_byte_work_units=sum(incurred.metadata_byte_work_units,n);need(sum(incurred.metadata_byte_work_units,publishing?0:terminal)<=l.metadata_byte_work_units,'association_budget');};
        const canon=v=>{const t=canonical(v);charge(byteLength(t));return t;};
        const hash=v=>{const t=canon(v);charge(byteLength(t));return sha(t);};
        const initial=canonical(context),contextBytes=byteLength(initial),metadata=10*contextBytes;charge(2*contextBytes);
        need(metadata<=l.metadata_bytes,'association_budget');
        need(s&&typeof s==='object'&&!Array.isArray(s)&&q&&typeof q==='object'&&!Array.isArray(q)
            &&Object.keys(s).sort().join(',')==='association,locator,span'&&Object.keys(q).sort().join(',')==='decision,expected,locator_expected,span_expected','association_metadata_invalid');
        for(const schema of Object.values(s))supported(schema);
        need(['policy','expected','decision','result'].every(k=>s.association.$defs?.[k])&&['policy','expected','result','observation','candidate','gap'].every(k=>s.locator.$defs?.[k])
            &&['policy','expected','receipt','content','cost','flags'].every(k=>s.span.$defs?.[k]),'association_policy_invalid');
        checked(d,'policy',s.association,'association_policy_invalid');checked(lp,'policy',s.locator,'association_policy_invalid');checked(sp,'policy',s.span,'association_policy_invalid');
        const {expected:e,decision,locator_expected:le,span_expected:se}=q;
        checked(e,'expected',s.association,'association_expected_invalid');checked(decision,'decision',s.association,'association_decision_invalid');
        checked(le,'expected',s.locator,'association_expected_invalid');checked(se,'expected',s.span,'association_expected_invalid');
        need(d.version==='v1-private-supplied-row-span-association'&&d.model==='supplied_canonical_metadata_row_span_consistency_v1'
            &&d.input_kind==='two_canonical_json_strings_v1'&&d.method==='reviewed_retained_row_span_metadata_consistency_v1'
            &&d.purpose==='associate_one_retained_row_span'&&d.decision_version==='v1-pre-span-row-occurrence-decision'
            &&d.decision_purpose==='select_one_retained_row_occurrence'&&d.privacy==='selected_occurrence_hashes_local_spans_only_v1'
            &&d.result_status==='SELECTED_ROW_SPAN_METADATA_CONSISTENCY_RECORDED_NOT_ORIGIN_ATTESTATION'&&row&&row.limits&&row.resource_limits
            &&disabled(d)&&disabled(lp)&&disabled(sp)&&disabled(row),'association_policy_invalid');
        need(lp.version==='v1-private-supplied-row-candidate-locator'&&lp.model==='supplied_immutable_text_finite_row_occurrences_v1'
            &&lp.grammar==='finite_ascii_single_arrow_occurrences_v1'&&lp.span_scheme==='view_local_utf16_half_open_strict_utf8_slice_v1'
            &&lp.complete_status==='ROW_CANDIDATE_SCAN_COMPLETED_WITHIN_FINITE_MODEL_NOT_TABLE'&&lp.incomplete_status==='PARTIAL_ROW_CANDIDATE_SCAN_UNKNOWN_LEXICAL_TAIL_NOT_TABLE'
            &&sp.version==='v1-private-supplied-exact-span-selection'&&sp.method==='reviewed_exact_utf16_span_v1'
            &&sp.purpose==='record_supplied_exact_span'&&sp.boundary_policy==='codepoint_and_crlf_atomic_v1'
            &&sp.span_scheme==='supplied_text_local_utf16_utf8_half_open_v1'&&sp.receipt_status==='EXACT_SPAN_CONTENT_SELECTION_RECORDED_NOT_FILE_ATTESTATION'
            &&[lp,sp].every(p=>p.input_kind==='supplied_immutable_string_v1'&&p.text_policy==='strict_utf8_preserve_bom_shebang_crlf_v1')
            &&row.version==='v1-private-supplied-local-row-fragment'&&row.grammar==='literal_arrow_rows_ascii_v1','association_policy_invalid');
        const identities={policy:hash(d),schema:hash(s.association),locator_policy:hash(lp),locator_schema:hash(s.locator),span_policy:hash(sp),span_schema:hash(s.span),
            row:hash(row),image:hash(image),row_limits:hash(row.limits),locator_expected:hash(le),span_expected:hash(se),decision:hash(decision),expected:hash(e)};
        need(d.schema_sha256===identities.schema&&d.locator_policy_sha256===identities.locator_policy&&d.span_policy_sha256===identities.span_policy
            &&d.row_policy_sha256===identities.row&&d.image_policy_sha256===identities.image
            &&lp.schema_sha256===identities.locator_schema&&lp.image_policy_sha256===identities.image&&lp.row_grammar_policy_sha256===identities.row&&lp.cursor_limit_reference_sha256===identities.row_limits
            &&sp.schema_sha256===identities.span_schema&&sp.reference_policy_sha256===identities.row&&sp.image_policy_sha256===identities.image,'association_policy_invalid');
        const caps={locator_bytes:lp.limits.summary_bytes,span_receipt_bytes:sp.limits.receipt_bytes,metadata_bytes:Math.min(row.resource_limits.metadata_bytes,image.limits.metadata_bytes),
            output_bytes:Math.min(row.limits.summary_bytes,sp.limits.receipt_bytes),candidate_count:Math.min(row.limits.row_count,lp.limits.row_count),
            metadata_byte_work_units:Math.min(row.resource_limits.metadata_bytes,image.limits.metadata_bytes),image_bytes:Math.min(row.resource_limits.image_bytes,image.limits.image_bytes),
            operation_ms:Math.min(row.resource_limits.operation_ms,sp.limits.operation_ms,image.limits.capture_ms)};
        need(Object.keys(l).sort().join(',')===Object.keys(caps).sort().join(',')&&Object.keys(caps).every(k=>Number.isSafeInteger(caps[k])&&caps[k]>0&&l[k]<=caps[k]),'association_policy_invalid');
        if(deadline===undefined)deadline=started+l.operation_ms;
        need(Number.isFinite(deadline)&&started<deadline&&deadline<=started+l.operation_ms,'association_deadline');
        const check=()=>{const t=now();need(Number.isFinite(t)&&t>=last&&t<deadline,'association_deadline');last=t;};check();
        need(e.policy_sha256===identities.policy&&e.schema_sha256===identities.schema&&e.purpose===d.purpose
            &&e.locator_policy_sha256===le.policy_sha256&&le.policy_sha256===identities.locator_policy&&e.locator_schema_sha256===le.schema_sha256&&le.schema_sha256===identities.locator_schema
            &&e.span_policy_sha256===se.policy_sha256&&se.policy_sha256===identities.span_policy&&e.span_schema_sha256===se.schema_sha256&&se.schema_sha256===identities.span_schema
            &&e.locator_expected_sha256===identities.locator_expected&&e.span_expected_sha256===identities.span_expected&&e.decision_sha256===identities.decision,'association_identity');
        stage='input';need(typeof locatorText==='string'&&typeof spanText==='string','association_input_invalid');
        need(locatorText.length<=l.locator_bytes&&spanText.length<=l.span_receipt_bytes,'association_budget');
        const lb=byteLength(locatorText),sb=byteLength(spanText),inputs=sum(lb,sb),inputReserve=10*inputs,outputReserve=10*l.output_bytes;
        const reserved=sum(inputReserve,metadata,outputReserve);need(lb<=l.locator_bytes&&sb<=l.span_receipt_bytes&&reserved<=l.image_bytes,'association_budget');check();
        const parse=(text,size,pin)=>{
            charge(size);let raw=Buffer.from(text,'utf8');need(raw.toString('utf8')===text,'association_encoding_invalid');
            charge(size);const digest=sha(raw);incurred.input_bytes_hashed=sum(incurred.input_bytes_hashed,size);need(digest===pin,'association_identity');
            charge(size);let parsed;try{parsed=JSON.parse(text);}catch(error){if(error instanceof SyntaxError)throw new CompilerRowSpanAssociationRefusal('association_json_invalid');throw error;}
            charge(size);parsed=copy(parsed,'association_metadata_invalid',l.metadata_bytes);raw=null;
            need(canon(parsed)===text,'association_canonical_invalid');check();return parsed;
        };
        locator=parse(locatorText,lb,e.locator_result_sha256);spanReceipt=parse(spanText,sb,e.span_receipt_sha256);locatorText=spanText=null;
        stage='documents';checked(locator,'result',s.locator,'association_locator_invalid');checked(locator.observation,'observation',s.locator,'association_locator_invalid');
        checked(spanReceipt,'receipt',s.span,'association_span_invalid');checked(spanReceipt.content,'content',s.span,'association_span_invalid');
        checked(spanReceipt.cost,'cost',s.span,'association_span_invalid');checked(spanReceipt.flags,'flags',s.span,'association_span_invalid');
        const o=locator.observation,c=spanReceipt.content;
        need(locator.version===lp.version&&spanReceipt.version===sp.version&&spanReceipt.status===sp.receipt_status&&spanReceipt.purpose===sp.purpose
            &&c.method===sp.method&&c.boundary_policy===sp.boundary_policy&&c.span_scheme===sp.span_scheme
            &&locator.raw_source_retained===false&&locator.private_facts_cleared===true&&locator.absolute_mapping===null
            &&spanReceipt.raw_source_retained===false&&spanReceipt.private_refs_cleared===true
            &&Object.values(locator.flags).every(v=>v===false)&&Object.values(spanReceipt.flags).every(v=>v===false),'association_document_identity');
        need(locator.producer_policy_sha256===identities.locator_policy&&locator.producer_schema_sha256===identities.locator_schema&&locator.expected_sha256===identities.locator_expected
            &&spanReceipt.producer_policy_sha256===identities.span_policy&&spanReceipt.producer_schema_sha256===identities.span_schema&&spanReceipt.expected_sha256===identities.span_expected
            &&locator.observation_sha256===hash(o)&&spanReceipt.content_sha256===hash(c),'association_document_identity');
        const source=e.source,U=source.source_utf16_units,B=source.source_utf8_bytes;
        need(U>0&&B>=U&&B<=4*U&&U<=Math.min(lp.limits.scan_code_units,sp.limits.source_utf16_units)&&B<=Math.min(lp.resource_limits.source_utf8_bytes,sp.limits.source_utf8_bytes)
            &&sourceEqual(source,{...sourceOf(locator),source_text_utf8_sha256:locator.source_text_sha256})&&sourceEqual(source,sourceOf(c))
            &&sourceEqual(source,{...sourceOf(le),source_text_utf8_sha256:le.source_text_sha256})&&sourceEqual(source,sourceOf(se))&&sourceEqual(source,decision.source),'association_source_identity');
        need(locator.scan_code_units<=lp.limits.scan_code_units&&locator.token_count<=lp.limits.token_count&&locator.work_units<=lp.limits.work_units
            &&locator.fact_bytes<=lp.limits.fact_bytes&&locator.gap_bytes<=lp.limits.gap_bytes&&locator.name_utf8_bytes<=lp.limits.name_utf8_bytes
            &&locator.source_identity_bytes_hashed===B&&locator.known_inputs_reserved_bytes<=lp.resource_limits.image_bytes
            &&Object.values(spanReceipt.cost).every(v=>v<=sp.limits.image_bytes)&&spanReceipt.cost.source_identity_bytes_hashed===B
            &&spanReceipt.cost.mapping_code_units_visited===c.end_utf16&&spanReceipt.cost.mapping_work_units===sum(c.end_utf16,c.end_utf8)
            &&spanReceipt.cost.mapping_work_units<=sp.limits.mapping_work_units&&spanReceipt.cost.view_bytes_hashed===c.view_utf8_bytes,'association_document_identity');
        stage='coverage';
        need(o.candidate_count===o.candidates.length&&o.candidates.length<=l.candidate_count&&o.certified_prefix_end_utf16<=o.examined_through_utf16&&o.examined_through_utf16<=U
            &&o.certified_prefix_end_utf8<=o.examined_through_utf8&&o.examined_through_utf8<=B&&o.unscanned_tail_code_units===U-o.certified_prefix_end_utf16,'association_coverage_invalid');
        if(o.scan_complete_within_model){
            need(locator.status===lp.complete_status&&o.gap===null&&o.unknown_tail_candidate_count===0&&o.certified_prefix_end_utf16===U&&o.examined_through_utf16===U
                &&o.certified_prefix_end_utf8===B&&o.examined_through_utf8===B,'association_coverage_invalid');
        }else{
            need(locator.status===lp.incomplete_status&&o.gap!==null&&o.unknown_tail_candidate_count===null,'association_coverage_invalid');
            checked(o.gap,'gap',s.locator,'association_coverage_invalid');const g=o.gap;
            need(o.certified_prefix_end_utf16<=g.start_utf16&&g.start_utf16<=g.end_utf16&&g.end_utf16<=o.examined_through_utf16
                &&o.certified_prefix_end_utf8<=g.start_utf8&&g.start_utf8<=g.end_utf8&&g.end_utf8<=o.examined_through_utf8,'association_coverage_invalid');
        }
        let previousU=0,previousB=0;
        for(let index=0;index<o.candidates.length;index++){
            check();const item=o.candidates[index];checked(item,'candidate',s.locator,'association_candidate_invalid');
            need(item.occurrence_index===index&&previousU<=item.start_utf16&&item.start_utf16<item.end_utf16&&item.end_utf16<=o.certified_prefix_end_utf16
                &&previousB<=item.start_utf8&&item.start_utf8<item.end_utf8&&item.end_utf8<=o.certified_prefix_end_utf8
                &&item.view_utf16_units===item.end_utf16-item.start_utf16&&item.view_utf8_bytes===item.end_utf8-item.start_utf8
                &&item.view_utf16_units<=item.view_utf8_bytes&&item.view_utf8_bytes<=4*item.view_utf16_units
                &&item.column_utf16_0based<=item.start_utf16&&item.line_1based<=item.start_utf16+1&&item.single_row_grammar_policy_sha256===identities.row,'association_candidate_invalid');
            previousU=item.end_utf16;previousB=item.end_utf8;incurred.checked_candidate_count++;
        }
        stage='selection';need(e.occurrence_index<o.candidates.length,'association_selection_invalid');
        const selected=o.candidates[e.occurrence_index],candidateHash=hash(selected);
        need(e.candidate_sha256===candidateHash&&decision.candidate_sha256===candidateHash&&decision.occurrence_index===e.occurrence_index
            &&decision.version===d.decision_version&&decision.purpose===d.decision_purpose&&decision.locator_result_sha256===e.locator_result_sha256
            &&decision.span_version===sp.version&&decision.span_policy_sha256===identities.span_policy&&decision.span_schema_sha256===identities.span_schema
            &&decision.span_invocation_id===se.invocation_id&&se.purpose===sp.purpose,'association_decision_invalid');
        need(selectionEqual(e.selection,selected)&&selectionEqual(e.selection,decision.selection)&&selectionEqual(e.selection,c)
            &&se.start_utf16===selected.start_utf16&&se.end_utf16===selected.end_utf16&&c.view_utf16_units<=sp.limits.view_utf16_units&&c.view_utf8_bytes<=sp.limits.view_utf8_bytes,'association_selection_invalid');
        need(e.origin_ref_sha256===decision.origin_ref_sha256&&e.origin_ref_sha256===se.origin_ref_sha256&&e.origin_ref_sha256===spanReceipt.origin_ref_sha256
            &&e.selection_ref_sha256===identities.decision&&se.selection_ref_sha256===identities.decision&&spanReceipt.selection_ref_sha256===identities.decision,'association_reference_invalid');
        const coverage={locator_status:locator.status,scan_complete_within_model:o.scan_complete_within_model,observation_sha256:locator.observation_sha256,
            ...Object.fromEntries(['candidate_count','certified_prefix_end_utf16','certified_prefix_end_utf8','examined_through_utf16','examined_through_utf8','unscanned_tail_code_units','unknown_tail_candidate_count'].map(k=>[k,o[k]])),
            gap_sha256:o.gap===null?null:hash(o.gap),gap_code:o.gap===null?null:o.gap.code};
        const cost={locator_input_utf8_bytes:lb,span_input_utf8_bytes:sb,context_canonical_bytes:contextBytes,metadata_reserved_bytes:metadata,input_copies_reserved_bytes:inputReserve,
            output_reserved_bytes:outputReserve,known_inputs_reserved_bytes:reserved,prepublication_metadata_byte_work_units:incurred.metadata_byte_work_units,
            terminal_byte_work_reserved_units:terminal,input_bytes_hashed:incurred.input_bytes_hashed,checked_candidate_count:incurred.checked_candidate_count};
        result={version:d.version,status:d.result_status,method:d.method,purpose:d.purpose,producer_policy_sha256:identities.policy,producer_schema_sha256:identities.schema,expected_sha256:identities.expected,
            locator_result_sha256:e.locator_result_sha256,span_receipt_sha256:e.span_receipt_sha256,locator_expected_sha256:identities.locator_expected,span_expected_sha256:identities.span_expected,
            decision_sha256:identities.decision,occurrence_index:e.occurrence_index,candidate_sha256:candidateHash,source:sourceOf(source),selection:selectionOf(selected),span_content_sha256:spanReceipt.content_sha256,
            origin_ref_sha256:e.origin_ref_sha256,selection_ref_sha256:identities.decision,coverage,cost,raw_source_retained:false,private_metadata_cleared:true,flags:denied()};
        checked(result,'result',s.association,'association_result_invalid');stage='settlement';locator=spanReceipt=context=null;check();
        stage='publish';for(const v of [result.source,result.selection,coverage,cost,result.flags,result])Object.freeze(v);check();publishing=true;
        wire=canon(result);need(byteLength(wire)<=l.output_bytes,'association_budget');charge(byteLength(wire));output=Buffer.from(wire,'utf8');check();
    }catch(error){primary=error instanceof CompilerTextWindowRefusal?Object.assign(new CompilerRowSpanAssociationRefusal(error.code),{cause:error}):error;failed=true;}
    finally{locatorText=spanText=context=locator=spanReceipt=result=wire=null;if(failed)output=null;}
    if(failed){if(primary&&['object','function'].includes(typeof primary))failures.set(primary,{stage,receipt_emitted:false,...incurred});throw primary;}
    return output;
}
function failureObservation(error){const v=error&&['object','function'].includes(typeof error)?failures.get(error):null;return v?{...v}:null;}
function failureStage(error){return failureObservation(error)?.stage||'unknown';}
module.exports={associateSuppliedRowSpanMetadata,CompilerRowSpanAssociationRefusal,failureObservation,failureStage};

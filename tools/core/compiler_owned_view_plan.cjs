"use strict";
// Supplied metadata only. Conditional costs never manufacture a grant or private owner.
const crypto=require('node:crypto'),{performance}=require('node:perf_hooks'),{types}=require('node:util');
const window=require('./compiler_text_window.cjs');
const {_canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate}=require('./compiler_stream_stager.cjs');
const PROFILE='private_compiler_owned_view_plan',failures=new WeakMap();
class CompilerOwnedViewPlanRefusal extends Error{constructor(code){super(code);this.code=code;}}
const need=(v,c)=>{if(!v)throw new CompilerOwnedViewPlanRefusal(c);};
function field(v,k){need(v&&typeof v==='object'&&!types.isProxy(v),'plan_policy_invalid');const d=Object.getOwnPropertyDescriptor(v,k);need(d&&Object.hasOwn(d,'value'),'plan_policy_invalid');return d.value;}
function sum(...values){need(values.every(v=>Number.isSafeInteger(v)&&v>=0),'plan_budget');const n=values.reduce((a,b)=>a+b,0);need(Number.isSafeInteger(n),'plan_budget');return n;}
function supported(s){
    const keys=['$schema','$id','title','$defs','$ref','type','required','properties','additionalProperties','const','enum','items','minItems','minLength','minimum','pattern'];
    need(s&&typeof s==='object'&&!Array.isArray(s)&&Object.keys(s).every(k=>keys.includes(k)),'plan_policy_invalid');
    for(const m of [s.$defs,s.properties])if(m)for(const v of Object.values(m))supported(v);if(s.items)supported(s.items);
}
function planSuppliedOwnedViewMetadata(expectedInput,profilesInput,schemasInput,options){
    let context=null,base=null,receipt=null,wire=null,output=null,stage='start',work=0,terminal=0,workCap=0,primary,failed=false;
    const size=v=>typeof v==='string'?Buffer.byteLength(v,'utf8'):v.length;
    try{
        const now=options?.now||(()=>performance.now());need(typeof now==='function','plan_deadline');
        const started=now();let last=started;const deadline=options?.deadline;
        need(Number.isFinite(started)&&Number.isFinite(deadline)&&started<deadline,'plan_deadline');
        const check=()=>{const t=now();need(Number.isFinite(t)&&t>=last&&t<deadline,'plan_deadline');last=t;};check();stage='policy';
        const ceiling=field(field(field(profilesInput,'private_input_image_experiment'),'limits'),'metadata_bytes');
        need(Number.isSafeInteger(ceiling)&&ceiling>0,'plan_policy_invalid');
        context=window.copy({expected:expectedInput,descriptor:field(profilesInput,PROFILE),profiles:Object.fromEntries([
            'private_compiler_text_owner','private_compiler_text_window','compiler_library_inputs','private_input_image_experiment',
            'private_compiler_row_span_composition','private_compiler_row_locator','private_compiler_span_selection','private_compiler_row_span_association','private_compiler_row_fragment'
        ].map(k=>[k,field(profilesInput,k)])),schemas:schemasInput},'plan_metadata_invalid',ceiling);
        const {expected:e,descriptor:d,profiles:p,schemas:s}=context,l=d.limits;
        need(l&&Number.isSafeInteger(l.metadata_byte_work_units)&&l.metadata_byte_work_units>0,'plan_policy_invalid');workCap=l.metadata_byte_work_units;
        const charge=n=>{work=sum(work,n);need(sum(work,terminal)<=workCap,'plan_budget');check();};
        const canon=v=>{check();const t=canonical(v);charge(2*Buffer.byteLength(t,'utf8'));return t;};
        const hash=v=>{const t=canon(v);charge(Buffer.byteLength(t,'utf8'));return crypto.createHash('sha256').update(t).digest('hex');};
        const checked=(v,k,sc,code)=>{check();validate(v,sc.$defs[k],sc,code);check();};
        need(s&&Object.keys(s).sort().join(',')==='composition,entry,planner,text_window','plan_policy_invalid');
        for(const sc of Object.values(s))supported(sc);
        checked(d,'policy',s.planner,'plan_policy_invalid');checked(e,'expected',s.planner,'plan_expected_invalid');
        need(d.version==='v1-private-owned-view-metadata-plan'&&d.model==='supplied_metadata_conditional_owned_view_phase_plan_v1'
            &&d.purpose==='plan_preselected_owned_view_metadata_only'&&Object.entries(d.dependency_pins).every(([k,v])=>v===hash(p[k])),'plan_identity');
        const disabled=v=>['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>v[k]===false);
        need(disabled(d)&&disabled(p.private_compiler_row_span_composition)&&disabled(p.private_compiler_text_owner),'plan_policy_invalid');
        const image=p.private_input_image_experiment,cp=p.private_compiler_row_span_composition,cl=cp.limits,cap=p.compiler_library_inputs;
        const refs={metadata_bytes:image.limits.metadata_bytes,metadata_byte_work_units:cl.parent_byte_work_units,output_bytes:cl.composition_receipt_bytes,
            image_bytes:Math.min(image.limits.image_bytes,cl.image_bytes),operation_ms:Math.min(image.limits.capture_ms,cl.operation_ms)};
        need(Object.keys(l).sort().join(',')===Object.keys(refs).sort().join(',')&&Object.entries(refs).every(([k,v])=>Number.isSafeInteger(l[k])&&l[k]>0&&l[k]<=v),'plan_policy_invalid');
        terminal=2*l.output_bytes;charge(0);need(deadline<=started+l.operation_ms,'plan_deadline');
        need(d.schema_sha256===hash(s.planner)&&e.schema_sha256===d.schema_sha256&&e.policy_sha256===hash(d)
            &&cp.schema_sha256===hash(s.composition),'plan_identity');
        stage='outer_metadata';base=window.prepare(null,e.base_request,p,{entry:s.entry,text_window:s.text_window});check();
        const outer=base.expected,selection=outer.selection,entry=outer.entry_expected,b=e.bindings,c=e.costs;
        need(b.base_request_sha256===hash(outer)&&b.root_sha256===outer.authority.root_sha256&&b.root_scheme===d.root_scheme
            &&b.selection_sha256===hash(selection)&&b.provenance_sha256===hash(outer.selection_provenance)
            &&b.capture_receipt_sha256===outer.selection_provenance.receipt_sha256&&b.current_scope_ref_sha256===outer.authority.authority_ref_sha256
            &&b.source_refs_sha256===hash(d.source_refs)&&[b.anchor_sha256,b.capture_envelope_sha256,b.current_scope_ref_sha256].every(v=>v!=='0'.repeat(64)),'plan_identity');
        stage='local_metadata';const local=e.local_expected;checked(local,'expected',s.composition,'plan_expected_invalid');
        need(local.policy_sha256===hash(cp)&&local.schema_sha256===cp.schema_sha256&&local.purpose===cp.purpose,'plan_identity');
        const source=local.source,r=local.candidate,V=selection.view_code_units,B=selection.view_utf8_bytes,N=selection.source_code_units,E=entry.entry_bytes;
        need(source.source_text_utf8_sha256===selection.view_text_sha256&&source.source_utf16_units===V&&source.source_utf8_bytes===B
            &&V<=cl.source_utf16_units&&B>=V&&B<=4*V&&B<=p.private_compiler_span_selection.limits.source_utf8_bytes
            &&E>=N&&E<=3*N&&r.start_utf16<r.end_utf16&&r.end_utf16<=V&&r.start_utf8<r.end_utf8&&r.end_utf8<=B
            &&r.view_utf16_units===r.end_utf16-r.start_utf16&&r.view_utf8_bytes===r.end_utf8-r.start_utf8
            &&r.view_utf8_bytes>=r.view_utf16_units&&r.view_utf8_bytes<=4*r.view_utf16_units
            &&r.single_row_grammar_policy_sha256===hash(p.private_compiler_row_fragment),'plan_identity');
        const core=Object.fromEntries(Object.entries(local).filter(([k])=>k!=='origin_ref_sha256'));
        need(hash(core)===b.local_core_sha256,'plan_identity');
        const seed={version:d.intent_version,purpose:d.purpose,base_request_sha256:b.base_request_sha256,bindings:b,
            local_core_sha256:b.local_core_sha256,consumer:d.consumer,budget_model:d.budget_model,source_refs:d.source_refs};
        const origin=hash(seed);need(local.origin_ref_sha256===origin,'plan_identity');
        stage='budget';const ll=p.private_compiler_row_locator.limits,lr=p.private_compiler_row_locator.resource_limits,sl=p.private_compiler_span_selection.limits,al=p.private_compiler_row_span_association.limits;
        const pos={u:ll.scan_code_units,b:lr.source_utf8_bytes,line:ll.scan_code_units+1,column:ll.scan_code_units};
        const token=8*Buffer.byteLength(canon({kind:'string',value:'',start:pos,end:pos}),'utf8');
        const J=sum(2*lr.source_utf8_bytes,4*ll.scan_code_units,lr.metadata_bytes,4*ll.cursor_bytes,2*token,5*ll.fact_bytes,5*ll.gap_bytes,5*ll.summary_bytes,
            2*sl.source_utf8_bytes,4*sl.source_utf16_units,sl.metadata_bytes,2*sl.cursor_bytes,10*sl.receipt_bytes,
            10*(al.locator_bytes+al.span_receipt_bytes),al.metadata_bytes,10*al.output_bytes,
            Math.min(p.private_compiler_row_fragment.resource_limits.metadata_bytes,image.limits.metadata_bytes),
            10*sum(ll.summary_bytes,sl.receipt_bytes,al.output_bytes,Math.min(ll.summary_bytes,sl.receipt_bytes,al.output_bytes)),2*Math.min(ll.scan_code_units,sl.source_utf16_units));
        const fixed=Buffer.byteLength(canon(context),'utf8'),intrinsic=sum(10*fixed,10*l.output_bytes);
        const X=sum(...Object.values(c.capture_forms)),Q=sum(intrinsic,...Object.values(c.wrapper_forms)),W=sum(...Object.values(c.wire_forms)),F=outer.authority.max_open_attempts;
        need(intrinsic<=l.metadata_bytes&&Q<=l.metadata_bytes&&sum(intrinsic,terminal)<=workCap&&F===5
            &&c.payload_bytes_cap===outer.authority.max_total_bytes&&c.payload_bytes_cap>=E&&c.payload_bytes_cap<=cap.max_total_bytes
            &&c.owner_metadata_cap<=image.limits.metadata_bytes&&sum(N,E)<=base.descriptor.whole_limits.verification_work_units
            &&sum(4,6*V)<=base.descriptor.whole_limits.selection_work_units,'plan_budget');
        const vector=K=>{
            const common=sum(K,2*N,F,Q,W),v={metadata_preflight:sum(Q,W),acquisition:sum(common,X),whole_verification:sum(common,E),
                view_check:sum(common,2*V,B),composition:sum(common,2*V,J),settlement:sum(common,2*V,J)};
            v.combined=c.overlap_model==='unknown_overlap_sum_v1'?sum(common,X,E,2*V,B,2*V,J):Math.max(...Object.values(v));
            need(Object.values(v).every(n=>n<=l.image_bytes),'plan_budget');return v;
        };
        const K=sum(c.payload_bytes_cap,c.owner_metadata_cap),prebound=vector(K);let supplied=null;
        if(e.snapshot!==null){
            stage='supplied_snapshot';const z=e.snapshot;checked(z,'snapshot',s.entry,'plan_snapshot_invalid');
            need(z.state==='READY'&&z.code===''&&z.entry_owner_registered===true&&z.entry_input_verified===true
                &&Object.entries(outer.entry).every(([k,v])=>z[k]===v)&&z.retained_entry_bytes===E&&z.retained_text_code_units===N
                &&z.capture_read_bytes>=E&&z.capture_read_bytes<=c.payload_bytes_cap&&z.metadata_bytes<=c.owner_metadata_cap
                &&z.known_owner_input_bytes===sum(z.capture_read_bytes,z.metadata_bytes),'plan_snapshot_invalid');
            supplied=vector(z.known_owner_input_bytes);
        }
        stage='settlement';const requestHash=hash(e),localHash=hash(local);
        receipt={version:d.version,status:d.result_status,request_sha256:requestHash,origin_intent_sha256:origin,local_expected_sha256:localHash,source_refs_sha256:b.source_refs_sha256,
            prebound,supplied_snapshot:supplied,dimensions:{K,E,N,V,B,F,X,Q,W,J},overlap_model:c.overlap_model,
            prepublication_metadata_work_units:work,terminal_metadata_work_reserved_units:terminal,planner_metadata_reserved_bytes:intrinsic,
            absolute_mapping:null,flags:Object.fromEntries(Object.keys(s.planner.$defs.flags.properties).map(k=>[k,false]))};
        checked(receipt,'receipt',s.planner,'plan_result_invalid');if(supplied!==null)checked(supplied,'phases',s.planner,'plan_result_invalid');
        wire=canonical(receipt);charge(2*Buffer.byteLength(wire,'utf8'));need(Buffer.byteLength(wire,'utf8')<=l.output_bytes,'plan_budget');
        output=Buffer.from(wire,'utf8');check();
    }catch(error){primary=error;failed=true;}
    finally{context=base=receipt=wire=null;if(failed)output=null;}
    if(failed){if(primary&&['object','function'].includes(typeof primary))failures.set(primary,{stage,metadata_byte_work_units:work,receipt_emitted:false,private_refs_cleared:true});throw primary;}
    return output;
}
function failureObservation(error){const v=error&&['object','function'].includes(typeof error)?failures.get(error):null;return v?{...v}:null;}
module.exports={planSuppliedOwnedViewMetadata,CompilerOwnedViewPlanRefusal,failureObservation};

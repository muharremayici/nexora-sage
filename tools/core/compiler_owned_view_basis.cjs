"use strict";
// Data-only partial cost linkage; no owner, planner, producer, transport or filesystem entrypoint.
const crypto=require('node:crypto'),{performance}=require('node:perf_hooks'),{types}=require('node:util');
const window=require('./compiler_text_window.cjs');
const {_canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate}=require('./compiler_stream_stager.cjs');
const PROFILE='private_compiler_owned_view_basis',failures=new WeakMap();
class CompilerOwnedViewBasisRefusal extends Error{constructor(code){super(code);this.code=code;}}
const need=(v,c)=>{if(!v)throw new CompilerOwnedViewBasisRefusal(c);};
function field(v,k){need(v&&typeof v==='object'&&!types.isProxy(v),'basis_policy_invalid');const d=Object.getOwnPropertyDescriptor(v,k);need(d&&Object.hasOwn(d,'value'),'basis_policy_invalid');return d.value;}
function sum(...values){need(values.every(v=>Number.isSafeInteger(v)&&v>=0),'basis_budget');const n=values.reduce((a,b)=>a+b,0);need(Number.isSafeInteger(n),'basis_budget');return n;}
function product(a,b){need([a,b].every(v=>Number.isSafeInteger(v)&&v>=0),'basis_budget');const n=a*b;need(Number.isSafeInteger(n),'basis_budget');return n;}
function supported(s){
    const keys=['$schema','$id','title','$defs','$ref','type','required','properties','additionalProperties','const','enum','items','minItems','minLength','minimum','pattern'];
    need(s&&typeof s==='object'&&!Array.isArray(s)&&Object.keys(s).every(k=>keys.includes(k)),'basis_policy_invalid');
    for(const m of [s.$defs,s.properties])if(m)for(const v of Object.values(m))supported(v);if(s.items)supported(s.items);
}
function checkSuppliedOwnedViewCostBasis(expectedInput,profilesInput,schemasInput,options){
    let context=null,base=null,receipt=null,wire=null,output=null,work=0,terminal=0,stage='start',primary,failed=false;
    try{
        const now=options?.now||(()=>performance.now());need(typeof now==='function','basis_deadline');
        const started=now();let last=started;const deadline=options?.deadline;
        need(Number.isFinite(started)&&Number.isFinite(deadline)&&started<deadline,'basis_deadline');
        const check=()=>{const t=now();need(Number.isFinite(t)&&t>=last&&t<deadline,'basis_deadline');last=t;};check();stage='policy';
        const ceiling=field(field(field(profilesInput,'private_input_image_experiment'),'limits'),'metadata_bytes');
        need(Number.isSafeInteger(ceiling)&&ceiling>0,'basis_policy_invalid');
        const first=field(profilesInput,PROFILE),pins=field(first,'dependency_pins');
        need(pins&&typeof pins==='object'&&!types.isProxy(pins),'basis_policy_invalid');
        const names=Object.keys(pins);need(names.every(k=>typeof Object.getOwnPropertyDescriptor(pins,k)?.value==='string'),'basis_policy_invalid');
        context=window.copy({expected:expectedInput,descriptor:first,profiles:Object.fromEntries(names.map(k=>[k,field(profilesInput,k)])),schemas:schemasInput},'basis_metadata_invalid',ceiling);
        const {expected:e,descriptor:d,profiles:p,schemas:s}=context,l=d.limits;
        need(l&&Number.isSafeInteger(l.metadata_byte_work_units)&&l.metadata_byte_work_units>0,'basis_policy_invalid');
        const charge=n=>{work=sum(work,n);need(sum(work,terminal)<=l.metadata_byte_work_units,'basis_budget');check();};
        const canon=v=>{check();const t=canonical(v);charge(2*Buffer.byteLength(t,'utf8'));return t;};
        const hash=v=>{const t=canon(v);charge(Buffer.byteLength(t,'utf8'));return crypto.createHash('sha256').update(t).digest('hex');};
        const checked=(v,k,sc,code)=>{check();validate(v,sc.$defs[k],sc,code);check();};
        const sameSet=(a,b,code)=>{need(a.length===b.length&&new Set(a).size===a.length&&a.every(k=>b.includes(k)),code);};
        need(s&&Object.keys(s).sort().join(',')==='basis,composition,entry,planner,text_window','basis_policy_invalid');for(const sc of Object.values(s))supported(sc);
        checked(d,'policy',s.basis,'basis_policy_invalid');checked(e,'expected',s.basis,'basis_expected_invalid');
        need(d.version==='v1-private-cost-basis-linkage-checker'&&d.model==='supplied_partial_cost_basis_linkage_v1'
            &&d.purpose==='check_supplied_cost_basis_linkage_only'&&d.status==='COST_BASIS_LINKAGE_INCOMPLETE_NOT_A_PLAN','basis_policy_invalid');
        need(d.parsed_subset_rule.formula_id==='disjoint_json_primitive_payload_8R_v1','basis_policy_invalid');
        need(d.token_subset_rule.formula_id==='duplicate_key_token_views_8R_v1'
            &&d.codec_subset_rule.formula_id==='reviver_roundtrip_role_decimal_3r1d_v1','basis_policy_invalid');
        need(Object.entries(s.basis.$defs.flags.properties).every(([k,v])=>v.const===(k==='linkage_checked')),'basis_policy_invalid');
        need(['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>d[k]===false),'basis_policy_invalid');
        need(Object.entries(d.dependency_pins).every(([k,v])=>v===hash(p[k])),'basis_identity');
        const planner=p.private_compiler_owned_view_plan,ref=planner.limits,image=p.private_input_image_experiment;
        need(Object.keys(l).sort().join(',')===Object.keys(ref).sort().join(',')&&Object.entries(ref).every(([k,v])=>Number.isSafeInteger(l[k])&&l[k]>0&&l[k]<=v),'basis_policy_invalid');
        need(l.metadata_bytes<=image.limits.metadata_bytes&&l.image_bytes<=image.limits.image_bytes&&l.operation_ms<=image.limits.capture_ms,'basis_policy_invalid');
        terminal=2*l.output_bytes;charge(0);need(deadline<=started+l.operation_ms,'basis_deadline');
        need(d.schema_sha256===hash(s.basis)&&e.schema_sha256===d.schema_sha256&&e.policy_sha256===hash(d)&&planner.schema_sha256===hash(s.planner),'basis_identity');
        const b=e.basis,t=e.planner_request;
        checked(t,'expected',s.planner,'basis_request_invalid');checked(t.local_expected,'expected',s.composition,'basis_request_invalid');
        if(t.snapshot!==null)checked(t.snapshot,'snapshot',s.entry,'basis_request_invalid');
        need(b.header.schema_sha256===d.schema_sha256&&hash(b.policy_references)===hash(d.dependency_pins)
            &&hash(b.source_references)===hash(d.source_refs),'basis_identity');
        need(d.source_refs.entry_owner===planner.source_refs.owner&&d.source_refs.text_window===planner.source_refs.window
            &&d.source_refs.composition===planner.source_refs.composition,'basis_identity');
        const groups=Object.entries(s.planner.$defs.costs.properties).filter(([,v])=>v.$ref).map(([k])=>k);
        const terms=groups.flatMap(g=>Object.keys(s.planner.$defs[g].properties).map(k=>({term_id:g+'.'+k,group:g,key:k})));
        sameSet(groups,d.form_groups,'basis_policy_invalid');
        const byTerm=(a,b)=>a.term_id<b.term_id?-1:a.term_id>b.term_id?1:0;
        need(hash(terms.slice().sort(byTerm))===hash(d.term_map.slice().sort(byTerm)),'basis_policy_invalid');
        stage='scope';base=window.prepare(null,t.base_request,p,{entry:s.entry,text_window:s.text_window});check();
        const outer=base.expected,ee=outer.entry_expected,sel=outer.selection,tb=t.bindings,sc=b.scope_references;
        need(t.schema_sha256===planner.schema_sha256&&t.policy_sha256===hash(planner),'basis_identity');
        const baseHash=hash(outer),selectionHash=hash(sel),provenanceHash=hash(outer.selection_provenance);
        const expectedScope={base_request_sha256:baseHash,entry_context_sha256:outer.entry.context_sha256,selection_sha256:selectionHash,
            provenance_sha256:provenanceHash,root_sha256:outer.authority.root_sha256,current_scope_ref_sha256:outer.authority.authority_ref_sha256,
            capture_receipt_sha256:outer.selection_provenance.receipt_sha256,capture_envelope_sha256:tb.capture_envelope_sha256,anchor_sha256:tb.anchor_sha256,root_scheme:planner.root_scheme};
        need(hash(sc)===hash(expectedScope)&&Object.entries(expectedScope).filter(([k])=>k!=='entry_context_sha256').every(([k,v])=>tb[k]===v)
            &&tb.source_refs_sha256===hash(planner.source_refs),'basis_identity');
        need([sc.current_scope_ref_sha256,sc.capture_envelope_sha256,sc.anchor_sha256].every(v=>v!=='0'.repeat(64)),'basis_identity');
        const local=t.local_expected,cp=p.private_compiler_row_span_composition;
        need(local.policy_sha256===hash(cp)&&local.schema_sha256===cp.schema_sha256&&local.purpose===cp.purpose
            &&local.source.source_text_utf8_sha256===sel.view_text_sha256&&local.source.source_utf16_units===sel.view_code_units
            &&local.source.source_utf8_bytes===sel.view_utf8_bytes,'basis_identity');
        stage='caps';const c=b.caps,cap=p.compiler_library_inputs;
        need(c.authority_total_cap_bytes===outer.authority.max_total_bytes&&c.authority_total_cap_bytes===t.costs.payload_bytes_cap
            &&c.owner_metadata_cap_bytes===t.costs.owner_metadata_cap&&c.owner_metadata_cap_bytes<=image.limits.metadata_bytes
            &&c.entry_bytes_expected===ee.entry_bytes&&c.whole_source_utf16_units===sel.source_code_units
            &&c.view_utf16_units===sel.view_code_units&&c.view_utf8_bytes===sel.view_utf8_bytes,'basis_identity');
        sameSet(Object.keys(c.role_caps),Object.keys(d.role_expected_fields),'basis_roles_invalid');
        let roleTotal=0,metadataTotal=0,codecBound=0;
        for(const [role,k]of Object.entries(d.role_expected_fields)){
            check();const r=c.role_caps[role];need(r.expected_identity_sha256===ee[k]&&r.cap_ref_sha256!=='0'.repeat(64),'basis_identity');
            need(r.raw_cap_bytes<=(role===d.entry_role?cap.max_module_bytes:cap.max_file_bytes),'basis_caps_invalid');
            if(role===d.entry_role)need(r.raw_cap_bytes>=ee.entry_bytes,'basis_caps_invalid');
            roleTotal=sum(roleTotal,r.raw_cap_bytes);
            if(d.metadata_roles.includes(role)){
                metadataTotal=sum(metadataTotal,r.raw_cap_bytes);
                let remaining=r.raw_cap_bytes,digits=1;
                while(remaining>=10){check();remaining=Math.floor(remaining/10);digits++;}
                codecBound=sum(codecBound,product(product(3,r.raw_cap_bytes),sum(1,digits)));
            }
        }
        need(outer.authority.max_open_attempts===Object.keys(d.role_expected_fields).length&&roleTotal<=c.authority_total_cap_bytes
            &&c.unassigned_scope_headroom_bytes===c.authority_total_cap_bytes-roleTotal,'basis_caps_invalid');
        const capsHash=hash(c),sourceHash=hash(b.source_references),decodedBound=sum(metadataTotal,metadataTotal,metadataTotal,metadataTotal);
        // The disjoint primitive-token model reserves8R; full native parsed storage stays unknown.
        const parsedBound=sum(decodedBound,decodedBound);
        // Four independently reserved token/key views use the same8R arithmetic, with a distinct destination.
        const tokenBound=parsedBound;
        stage='records';const termIds=d.term_map.map(v=>v.term_id);
        sameSet(b.unknowns.term_ids,termIds,'basis_coverage_invalid');sameSet(b.unknowns.failure_path_ids,d.failure_ids,'basis_coverage_invalid');
        sameSet(b.coverage.failure_path_ids,d.failure_ids,'basis_coverage_invalid');
        need(b.coverage.overlap_model===t.costs.overlap_model,'basis_coverage_invalid');
        sameSet(b.coverage.exclusion_ids,[...new Set(b.coverage.exclusion_ids)],'basis_coverage_invalid');
        let subset=null,parsedSubset=null,tokenSubset=null,codecSubset=null;
        for(const item of d.term_map){
            check();const r=b.representations[item.group][item.key];
            need(r.unit==='logical_bytes'&&r.upper_bound_bytes===null&&r.zero_absence_ref_sha256===null
                &&r.term_id===item.term_id&&r.destination.group===item.group&&r.destination.key===item.key
                &&r.input_caps_sha256===capsHash&&r.source_closure_sha256===sourceHash&&r.overlap_condition===b.coverage.overlap_model,'basis_identity');
            need(Object.values(r.phase_coverage).every(v=>v==='unknown')&&Object.values(r.failure_coverage).every(v=>v==='unknown'),'basis_coverage_invalid');
            sameSet(r.exclusion_refs,[...new Set(r.exclusion_refs)],'basis_coverage_invalid');need(r.exclusion_refs.every(k=>b.coverage.exclusion_ids.includes(k)),'basis_coverage_invalid');
            if(r.model_id===d.models.decoded_subset){
                need(r.term_id===d.subset_term_id&&r.coverage_status==='partial'&&r.basis_kind==='derived_from_supplied_caps'
                    &&r.subset_upper_bound_bytes===decodedBound&&r.observed_bytes===null,'basis_model_invalid');subset=decodedBound;
            }else if(r.model_id===d.models.parsed_subset){
                need(r.term_id===d.parsed_subset_rule.term_id&&r.coverage_status==='partial'&&r.basis_kind==='derived_from_supplied_caps'
                    &&r.subset_upper_bound_bytes===parsedBound&&r.observed_bytes===null,'basis_model_invalid');parsedSubset=parsedBound;
            }else if(r.model_id===d.models.token_subset){
                need(r.term_id===d.token_subset_rule.term_id&&r.coverage_status==='partial'&&r.basis_kind==='derived_from_supplied_caps'
                    &&r.subset_upper_bound_bytes===tokenBound&&r.observed_bytes===null,'basis_model_invalid');tokenSubset=tokenBound;
            }else if(r.model_id===d.models.codec_subset){
                need(r.term_id===d.codec_subset_rule.term_id&&r.coverage_status==='partial'&&r.basis_kind==='derived_from_supplied_caps'
                    &&r.subset_upper_bound_bytes===codecBound&&r.observed_bytes===null,'basis_model_invalid');codecSubset=codecBound;
            }else{
                need(r.model_id===d.models.unresolved&&r.coverage_status==='unknown'&&r.subset_upper_bound_bytes===null
                    &&r.basis_kind!=='derived_from_supplied_caps','basis_model_invalid');
                need((r.basis_kind==='retained_observation')===(r.observed_bytes!==null),'basis_model_invalid');
            }
        }
        stage='linkage';const basisHash=hash(b),requestHash=hash(t);
        need(basisHash===e.expected_basis_sha256&&requestHash===e.expected_request_sha256,'basis_anchor_mismatch');
        const costsHash=hash(t.costs),sidecar={basis_sha256:basisHash,expected_basis_sha256:e.expected_basis_sha256,
            planner_request_sha256:requestHash,planner_policy_sha256:t.policy_sha256,planner_schema_sha256:t.schema_sha256,costs_sha256:costsHash};
        const sidecarHash=hash(sidecar),fixed=Buffer.byteLength(canon(context),'utf8'),reserved=sum(10*fixed,10*l.output_bytes);
        need(reserved<=l.metadata_bytes&&reserved<=l.image_bytes&&sum(reserved,terminal)<=l.metadata_byte_work_units,'basis_budget');
        stage='settlement';receipt={version:d.version,status:d.status,basis_sha256:basisHash,expected_basis_sha256:e.expected_basis_sha256,
            planner_request_sha256:requestHash,costs_sha256:costsHash,source_closure_sha256:sourceHash,input_caps_sha256:capsHash,sidecar_sha256:sidecarHash,
            unresolved_term_ids:termIds,unknown_failure_path_ids:d.failure_ids.slice(),checked_term_count:termIds.length,declared_complete:false,
            decoded_subset_upper_bound_bytes:subset,parsed_subset_upper_bound_bytes:parsedSubset,
            token_subset_upper_bound_bytes:tokenSubset,codec_subset_upper_bound_bytes:codecSubset,
            cap_summary:{role_cap_total_bytes:roleTotal,authority_total_cap_bytes:c.authority_total_cap_bytes,
                unassigned_scope_headroom_bytes:c.unassigned_scope_headroom_bytes,eof_backing_roles:Object.keys(d.role_expected_fields).length},
            planner_invocations:0,planner_validation_performed:false,prepublication_metadata_work_units:work,terminal_metadata_work_reserved_units:terminal,
            metadata_reserved_bytes:reserved,absolute_mapping:null,flags:Object.fromEntries(Object.keys(s.basis.$defs.flags.properties).map(k=>[k,k==='linkage_checked']))};
        checked(receipt,'receipt',s.basis,'basis_result_invalid');wire=canonical(receipt);charge(2*Buffer.byteLength(wire,'utf8'));
        need(Buffer.byteLength(wire,'utf8')<=l.output_bytes,'basis_budget');output=Buffer.from(wire,'utf8');check();
    }catch(error){primary=error;failed=true;}
    finally{context=base=receipt=wire=null;if(failed)output=null;}
    if(failed){if(primary&&['object','function'].includes(typeof primary))failures.set(primary,{stage,metadata_byte_work_units:work,receipt_emitted:false,private_refs_cleared:true});throw primary;}
    return output;
}
function failureObservation(error){const v=error&&['object','function'].includes(typeof error)?failures.get(error):null;return v?{...v}:null;}
module.exports={checkSuppliedOwnedViewCostBasis,CompilerOwnedViewBasisRefusal,failureObservation};

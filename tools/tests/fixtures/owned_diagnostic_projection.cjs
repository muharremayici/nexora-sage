'use strict';
// Optional private diagnostic metadata owner. No filesystem or producer activation.
const crypto=require('node:crypto');
const {canonical}=require('../../core/owned_image_handoff.cjs');
const {sourceRecords}=require('../../core/owned_declaration_handoff.cjs');
const {diagnosticPayload}=require('../../core/typescript_diagnostic_payload.cjs');
const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
const fail=code=>{const error=new Error(code);error.code=code;throw error;};
const digest=value=>sha(canonical(value));
const flags={native_project_equivalent:false,semantic_lineage:false,production_enabled:false};
const same=(a,b)=>canonical(a)===canonical(b);

function checkDiagnosticProjectionPolicy(admitted,policies){
    const context=policies.diagnosticProjection,policy=context?.policy;
    if(!policy||policy.version!=='v1-private-owned-diagnostic-projection'
        ||['default_enabled','production_enabled','semantic_lineage','cache_eligible','raw_messages_retained'].some(key=>policy[key]!==false)
        ||policy.diagnostic_calls_from_input!=='max_programs'||policy.metadata_limit_from_input_image!=='metadata_bytes'
        ||policy.formatter_contract!=='v1-six-field-typescript-diagnostic'||!context.input
        ||digest(context.input)!==digest(policies.joined)||digest(policies.joined)!==admitted.binding.handoff_policy_sha256
        ||digest(policies.image)!==admitted.binding.image_policy_sha256
        ||policies.joined[policy.diagnostic_calls_from_input]!==1)fail('diagnostic_projection_policy_refused');
    return policy;
}

const diagnosticFlags={...flags,cache_eligible:false,program_completeness_established:false,raw_messages_retained:false};

function createDiagnosticTap(next,admitted,policies,mapping,lane){
    const policy=checkDiagnosticProjectionPolicy(admitted,policies),outer=JSON.parse(canonical(admitted.binding));
    const identity=admitted.bindingSha256,sourceInputs=sourceRecords(admitted,policies.joined);
    if(identity!==digest(outer)||outer.compiler_version!==next.version)fail('diagnostic_projection_context_refused');
    const binding=Object.freeze({atlas_snapshot_id:outer.atlas_snapshot_id,atlas_sha256:outer.atlas_sha256,handoff_sha256:identity,
        input_policy_sha256:outer.handoff_policy_sha256,image_policy_sha256:outer.image_policy_sha256,
        projection_policy_sha256:digest(policy),compiler_source_sha256:outer.compiler_source_sha256,compiler_version:outer.compiler_version});
    const cap=policies.image.limits[policy.metadata_limit_from_input_image],api=Object.create(next);
    let calls=0,reported=null,rows=[],facts=[],code='',correspondenceOwner=null;
    const refusal=reason=>{code=code||reason;rows=[];};
    function current(){
        checkDiagnosticProjectionPolicy(admitted,policies);
        if(digest(admitted.binding)!==identity||digest(sourceRecords(admitted,policies.joined))!==digest(sourceInputs)
            ||digest(policies.diagnosticProjection.policy)!==binding.projection_policy_sha256)fail('diagnostic_projection_context_refused');
    }
    Object.defineProperty(api,'getPreEmitDiagnostics',{value:program=>{
        calls++;
        if(correspondenceOwner){const held=ownerFrames.get(correspondenceOwner);if(held)delete held.arms[lane];}
        let diagnostics;
        try{diagnostics=next.getPreEmitDiagnostics(program);}
        catch(error){refusal('diagnostic_original_call_failed');throw error;}
        reported=Array.isArray(diagnostics)?diagnostics.length:null;
        try{
            current();
            if(calls!==policies.joined[policy.diagnostic_calls_from_input])fail('diagnostic_call_budget_refused');
            if(reported===null)fail('diagnostic_list_refused');
            if(code)return diagnostics;
            const projected=[],observedFacts=[],expectedRows=[];let size=2;
            for(const diag of diagnostics){
                if(!diag||!Number.isSafeInteger(diag.code)||diag.code<0||!Number.isInteger(diag.category))fail('diagnostic_shape_refused');
                let owner=null,start=null;
                if(diag.file){
                    if(correspondenceOwner&&program.getSourceFile(diag.file.fileName)!==diag.file)fail('diagnostic_program_membership_refused');
                    owner=mapping.role(diag.file.fileName,lane);
                    if(!(owner.role.startsWith('source:')||owner.role==='declaration'||owner.role.startsWith('compiler:')||owner.role.startsWith('package:'))
                        ||owner.kind&&owner.kind!=='declaration')fail('diagnostic_role_refused');
                    if(typeof diag.file.text!=='string'||sha(diag.file.text)!==owner.host_sha256)fail('diagnostic_source_mismatch');
                    const source=Object.values(sourceInputs).find(row=>row.role===owner.role);
                    if(owner.role.startsWith('source:')&&(!source||source.path!==owner.virtual
                        ||source.host_sha256!==owner.host_sha256||source.raw_sha256!==owner.raw_sha256))fail('diagnostic_source_mismatch');
                    start=diag.start;
                    if(!Number.isSafeInteger(start)||start<0||start>diag.file.text.length
                        ||start<diag.file.text.length&&/[\udc00-\udfff]/.test(diag.file.text[start]))fail('diagnostic_position_refused');
                }
                const projectRoot=lane==='image'?'/__sage_ts_image__/project':mapping.rows.find(row=>row.role==='config').physical.replace(/[\\/][^\\/]+$/,'');
                const row=diagnosticPayload(next,diag,projectRoot);
                if(!Number.isSafeInteger(row.line)||row.line<1||!Number.isSafeInteger(row.character)||row.character<1)fail('diagnostic_position_refused');
                const messageBytes=Buffer.byteLength(row.message,'utf8');
                if(messageBytes>cap)fail('diagnostic_metadata_budget_refused');
                const observed={code:row.code,category:row.category,source_role:owner?.role||null,
                    source_raw_sha256:owner?.raw_sha256||null,source_host_sha256:owner?.host_sha256||null,
                    file_sha256:sha(row.file),line:row.line,character:row.character,reported_start:start,
                    message_sha256:sha(row.message),message_utf8_bytes:messageBytes};
                size+=Buffer.byteLength(JSON.stringify(observed),'utf8')+1;
                if(size>cap)fail('diagnostic_metadata_budget_refused');
                // Capture upstream scalars before the outbound sidecar exists; never read it as an oracle.
                if(correspondenceOwner)expectedRows.push(Object.freeze({...observed}));
                projected.push(observed);
                observedFacts.push({role:owner?.role||null,code:diag.code,category:diag.category,start});
            }
            if(correspondenceOwner){const held=ownerFrames.get(correspondenceOwner);held.current();held.arms[lane]=Object.freeze({
                rows:Object.freeze(expectedRows),diagnostic_calls:calls,reported_count:reported});}
            rows=projected;facts=observedFacts.sort((a,b)=>canonical(a).localeCompare(canonical(b)));
        }catch(error){refusal(error.code||'diagnostic_projection_failed');}
        return diagnostics; // Preserve the original array and every Diagnostic object.
    }});
    return {api,bindOwner(handle){
        const held=ownerFrames.get(handle);
        if(correspondenceOwner||calls||!held||!['image','native'].includes(lane))fail('diagnostic_owner_binding_refused');
        held.current();if(!same(binding,held.binding))fail('diagnostic_owner_binding_refused');correspondenceOwner=handle;
    },finish(baseArm=null){
        try{current();}catch(error){refusal(error.code||'diagnostic_projection_context_refused');}
        if(calls!==policies.joined[policy.diagnostic_calls_from_input]||reported===null)refusal('diagnostic_call_incomplete');
        if(!code&&baseArm&&!same(facts,baseArm.diagnostics))refusal('diagnostic_base_list_mismatch');
        const result={status:code?'REFUSED':'PROJECTED',code,diagnostic_calls:calls,reported_count:reported,
            projected_count:rows.length,rows,binding,...diagnosticFlags};
        if(Buffer.byteLength(JSON.stringify(result),'utf8')>cap){refusal('diagnostic_metadata_budget_refused');return {...result,status:'REFUSED',code,rows:[],projected_count:0};}
        return result;
    }};
}

// Invocation-local capability: a JSON object cannot manufacture a live owner expectation.
// This is private correspondence, not authentication of hostile in-process code.
const ownerFrames=new WeakMap();
function createDiagnosticCorrespondenceOwner(admitted,policies,instance){
    const projection=checkDiagnosticProjectionPolicy(admitted,policies),policy=policies.diagnosticCorrespondence?.policy;
    if(!policy||policy.version!=='v1-private-owned-diagnostic-correspondence'
        ||policy.projection_profile!=='private_owned_diagnostic_projection'
        ||['default_enabled','production_enabled','semantic_lineage','cache_eligible','raw_messages_retained'].some(k=>policy[k]!==false)
        ||policy.metadata_limit_from_input_image!==projection.metadata_limit_from_input_image
        ||!same(policies.diagnosticCorrespondence.projection,projection))fail('diagnostic_correspondence_policy_refused');
    const outer=JSON.parse(canonical(admitted.binding)),identity=admitted.bindingSha256;
    const sources=digest(sourceRecords(admitted,policies.joined)),summary=instance.inputIdentity(admitted.binding);
    if(identity!==digest(outer)||!Object.isFrozen(summary)
        ||!['image_sha256','envelope_sha256'].every(k=>/^[a-f0-9]{64}$/.test(summary[k])))fail('diagnostic_owner_identity_refused');
    const binding=Object.freeze({atlas_snapshot_id:outer.atlas_snapshot_id,atlas_sha256:outer.atlas_sha256,handoff_sha256:identity,
        input_policy_sha256:outer.handoff_policy_sha256,image_policy_sha256:outer.image_policy_sha256,
        projection_policy_sha256:digest(projection),compiler_source_sha256:outer.compiler_source_sha256,compiler_version:outer.compiler_version});
    const policyHash=digest(policy),projectionHash=digest(projection),handle=Object.freeze({});
    const held={alive:true,summary,binding,arms:{},expectedCalls:policies.joined[projection.diagnostic_calls_from_input],policy:JSON.parse(canonical(policy)),projection:JSON.parse(canonical(projection)),
        context:digest({binding,summary,invocation_id:outer.invocation_id,consumer_policy_sha256:policyHash}),
        current(){
            if(!held.alive)fail('diagnostic_owner_disposed');
            checkDiagnosticProjectionPolicy(admitted,policies);
            if(digest(admitted.binding)!==identity||digest(sourceRecords(admitted,policies.joined))!==sources
                ||digest(policies.diagnosticCorrespondence.policy)!==policyHash
                ||digest(policies.diagnosticProjection.policy)!==projectionHash
                ||instance.inputIdentity(admitted.binding)!==summary)fail('diagnostic_owner_context_changed');
        }};
    held.current();ownerFrames.set(handle,held);return handle;
}

function disposeDiagnosticCorrespondenceOwner(owner){
    const held=ownerFrames.get(owner);if(held){held.alive=false;held.arms={};}
    ownerFrames.delete(owner);
}

function checkDiagnosticCorrespondence(payload,owner){
    try{
        const held=ownerFrames.get(owner);if(!held)fail('diagnostic_owner_unavailable');held.current();
        const expected={};
        for(const lane of ['image','native']){
            const arm=held.arms[lane];
            if(!arm||arm.diagnostic_calls!==held.expectedCalls||arm.reported_count!==arm.rows.length)fail('diagnostic_owner_call_incomplete');
            expected[lane]={status:'PROJECTED',code:'',diagnostic_calls:arm.diagnostic_calls,reported_count:arm.reported_count,
                projected_count:arm.rows.length,rows:arm.rows,binding:held.binding,...diagnosticFlags};
        }
        if(!same(expected.image,expected.native))fail('diagnostic_owner_arms_mismatch');
        const wanted={version:held.projection.version,authority:'producer_observed_only',status:held.projection.success_status,
            code:'',matched:true,input_image_sha256:held.summary.image_sha256,input_envelope_sha256:held.summary.envelope_sha256,
            ...expected,...diagnosticFlags};
        if(!same(payload,wanted))fail('diagnostic_owner_payload_mismatch');
        return {matched:true,code:''};
    }catch(error){return {matched:false,code:error.code||'diagnostic_correspondence_invalid'};}
}

function attachDiagnosticCorrespondence(result,owner,policies){
    const checked=checkDiagnosticCorrespondence(result.diagnostic_projection,owner),held=ownerFrames.get(owner);
    const policy=policies.diagnosticCorrespondence.policy;
    const block={version:policy.version,authority:'producer_observed_only',...checked,
        status:checked.matched?policy.success_status:policy.refusal_status,
        owner_context_sha256:held?.context||null,consumer_policy_sha256:digest(policy),
        input_image_sha256:held?.summary.image_sha256||null,input_envelope_sha256:held?.summary.envelope_sha256||null,
        image_row_count:held?.arms.image?.rows.length??null,native_row_count:held?.arms.native?.rows.length??null,...diagnosticFlags};
    result.diagnostic_correspondence=block;
    const cap=Math.min(policies.bridge.max_response_bytes,policies.image.limits[policy.metadata_limit_from_input_image]);
    if(Buffer.byteLength(JSON.stringify(result),'utf8')>cap){
        Object.assign(block,{matched:false,status:policy.refusal_status,code:'diagnostic_combined_budget_refused',
            owner_context_sha256:null,consumer_policy_sha256:null,input_image_sha256:null,input_envelope_sha256:null,
            image_row_count:null,native_row_count:null});
        if(Buffer.byteLength(JSON.stringify(result),'utf8')>cap){
            delete result.diagnostic_correspondence;
            process.stderr.write('private_diagnostic_correspondence_refused: diagnostic_combined_budget_refused\n');
        }
    }
    return result;
}

function attachDiagnosticProjection(result,imageTap,nativeTap,rawImage,policies){
    const policy=policies.diagnosticProjection.policy,image=imageTap.finish(result.image),native=nativeTap.finish(result.native);
    const matched=image.status==='PROJECTED'&&native.status==='PROJECTED'&&same(image,native);
    const block={version:policy.version,authority:'producer_observed_only',status:matched?policy.success_status:policy.refusal_status,
        code:matched?'':image.code||native.code||'diagnostic_projection_mismatch',matched,
        input_image_sha256:rawImage.image_sha256,input_envelope_sha256:rawImage.envelope_sha256,image,native,...diagnosticFlags};
    result.diagnostic_projection=block;
    const cap=Math.min(policies.bridge.max_response_bytes,policies.image.limits[policy.metadata_limit_from_input_image]);
    if(Buffer.byteLength(JSON.stringify(result),'utf8')>cap){
        Object.assign(block,{status:policy.refusal_status,code:'diagnostic_combined_budget_refused',matched:false});
        for(const arm of [image,native])Object.assign(arm,{status:'REFUSED',code:block.code,rows:[],projected_count:0});
        if(Buffer.byteLength(JSON.stringify(result),'utf8')>cap){
            delete result.diagnostic_projection;
            process.stderr.write('private_diagnostic_projection_refused: diagnostic_combined_budget_refused\n');
        }
    }
    return result;
}

function diagnosticProjectionControl(ts,admitted,policies,mapping,control){
    const row=mapping.rows.find(row=>row.role==='source:entry');
    const file=ts.createSourceFile(row.virtual,admitted.text.replace(/^\ufeff/,''),ts.ScriptTarget.Latest,true);
    const diag={file,start:110,code:2322,category:ts.DiagnosticCategory.Error,messageText:'modeled message'};
    let original=[diag],originalFailure=new Error('modeled original call failure'),thrownSame=false,returned;
    if(control==='fileless'){delete diag.file;delete diag.start;}
    if(control==='duplicate')original=[diag,{...diag}];
    if(control==='source_text')file.text='different text';
    if(control==='foreign_path')file.fileName='/__sage_ts_image__/project/foreign.tsx';
    if(control==='wrong_role')row.role='config';
    if(control==='missing_start')delete diag.start;
    if(control==='surrogate_start')diag.start=file.text.indexOf('😀')+1;
    if(control==='budget')diag.messageText='x'.repeat(policies.image.limits.metadata_bytes+1);
    if(control==='invalid_list')original={};
    const fake=Object.create(ts);
    Object.defineProperty(fake,'getPreEmitDiagnostics',{value:()=>{if(control==='original_failure')throw originalFailure;return original;}});
    if(control==='format_error')Object.defineProperty(fake,'flattenDiagnosticMessageText',{value:()=>{throw new Error('modeled formatter failure');}});
    if(control==='parent')admitted={...admitted,binding:{...admitted.binding,atlas_sha256:sha('other parent')}};
    if(control==='handoff')admitted={...admitted,bindingSha256:sha('other handoff')};
    if(control==='input_policy')policies.diagnosticProjection.input={...policies.diagnosticProjection.input,max_programs:2};
    try{
        const tap=createDiagnosticTap(fake,admitted,policies,mapping,'image');
        if(control==='source_buffer')admitted.sourceInputs.entry.raw[0]^=1;
        if(control==='policy_changed')policies.diagnosticProjection.policy={...policies.diagnosticProjection.policy,production_enabled:true};
        if(control!=='missing_call'){
            try{returned=tap.api.getPreEmitDiagnostics({});}catch(error){thrownSame=error===originalFailure;}
            if(control==='repeat_call')returned=tap.api.getPreEmitDiagnostics({});
        }
        if(control==='altered_list')diag.code=999;
        const baseArm={diagnostics:[]};
        if(Array.isArray(original))baseArm.diagnostics=original.map(d=>({role:d.file?'source:entry':null,
            code:d.code,category:d.category,start:Number.isInteger(d.start)?d.start:null})).sort((a,b)=>canonical(a).localeCompare(canonical(b)));
        if(['combined_budget','projection_mismatch','projection_match'].includes(control)){
            const base={status:'FINITE_CONTEXTS_MATCHED_NOT_ADMITTED',image:baseArm,native:baseArm};
            let baseWithinBudget=true;
            if(control==='combined_budget'){
                base.padding='';
                base.padding='x'.repeat(policies.image.limits.metadata_bytes-Buffer.byteLength(JSON.stringify(base),'utf8')-1);
                baseWithinBudget=Buffer.byteLength(JSON.stringify(base),'utf8')<policies.image.limits.metadata_bytes;
            }
            const native=control==='projection_mismatch'?{finish:arm=>{const o=JSON.parse(canonical(tap.finish(arm)));o.rows[0]={...o.rows[0],message_sha256:sha('different')};return o;}}:tap;
            const result=attachDiagnosticProjection(base,tap,native,{image_sha256:sha('modeled image'),envelope_sha256:sha('modeled envelope')},policies);
            return {status:'DIAGNOSTIC_PROJECTION_MODELED_ONLY',projection:result.diagnostic_projection||null,
                base_status_preserved:result.status==='FINITE_CONTEXTS_MATCHED_NOT_ADMITTED',
                base_rows_preserved:result.image===baseArm&&result.native===baseArm,
                ...(control==='combined_budget'?{base_within_budget_before_sidecar:baseWithinBudget}:{}),
                real_compiler_invocations:0,raw_image_persisted:false};
        }
        const observation=tap.finish(baseArm);
        return {status:'DIAGNOSTIC_PROJECTION_MODELED_ONLY',observation,
            original_array_preserved:returned===original,original_throw_preserved:thrownSame,
            real_compiler_invocations:0,raw_image_persisted:false};
    }catch(error){return {status:'DIAGNOSTIC_PROJECTION_MODELED_REFUSAL',code:error.code||'diagnostic_model_error',real_compiler_invocations:0,raw_image_persisted:false};}
}

module.exports={checkDiagnosticProjectionPolicy,createDiagnosticTap,attachDiagnosticProjection,diagnosticProjectionControl,
    createDiagnosticCorrespondenceOwner,checkDiagnosticCorrespondence,disposeDiagnosticCorrespondenceOwner,attachDiagnosticCorrespondence};

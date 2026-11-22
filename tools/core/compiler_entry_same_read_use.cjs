'use strict';
// Trusted first-party composition only. No file API, evaluator or public owner.
const crypto=require('node:crypto'),{types}=require('node:util');
const textWindow=require('./compiler_text_window.cjs');
const { _canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate }=require('./compiler_stream_stager.cjs');
const PROFILE='private_compiler_entry_same_read_use',ID='v1-fixed-entry-text-digest-and-dimensions';
const VERSION='v1-private-synthetic-entry-same-read-use',STATUS='SYNTHETIC_SAME_READ_TEXT_USE_COMPLETED_NOT_COMPILER_ADMISSION';
const syntheticContract=Object.freeze({identityVersion:'v1-private-synthetic-entry-identity',identityScope:'temporary_synthetic_five_role_identity_only',
    identityStatus:'SYNTHETIC_FIVE_ROLE_IDENTITY_OBSERVED_NOT_COMPILER_ADMISSION',version:VERSION,status:STATUS,scope:'authored_temporary_in_process_fixed_text_use'});
const actualContract=Object.freeze({identityVersion:'v1-private-actual-purpose-entry-identity',identityScope:'declared_five_role_fixed_use_identity_only',
    identityStatus:'FIVE_ROLE_ACTUAL_PURPOSE_IDENTITY_OBSERVED_NOT_COMPILER_ADMISSION',version:'v1-private-actual-purpose-entry-use',
    status:'ACTUAL_PURPOSE_SAME_READ_TEXT_USE_COMPLETED_NOT_COMPILER_ADMISSION',scope:'declared_root_in_process_fixed_text_use'});
const MAX=Number.MAX_SAFE_INTEGER,failures=new WeakMap();
const hash=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
const bytes=v=>Buffer.byteLength(canonical(v),'utf8');
const OFF=['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'];
const FLAGS=['read_authority_granted','source_freshness_verified','physical_path_binding_verified','atomic_snapshot_verified',
    'compiler_loaded','input_code_evaluated','native_execution_attested','complete_compiler_inputs','snapshot_bound',
    'production_enabled','semantic_lineage','cache_eligible','publication_authority'];
function field(v,k,need){
    need(v&&typeof v==='object'&&!types.isProxy(v),'same_use_context');
    const d=Object.getOwnPropertyDescriptor(v,k);need(d&&Object.hasOwn(d,'value'),'same_use_context');return d.value;
}
function options(value,need){
    need(value&&typeof value==='object'&&!types.isProxy(value),'same_use_options');
    const ds=Object.getOwnPropertyDescriptors(value);
    need(Reflect.ownKeys(ds).every(k=>['now','deadline'].includes(k)&&Object.hasOwn(ds[k],'value')),'same_use_options');
    return Object.fromEntries(Object.entries(ds).map(([k,d])=>[k,d.value]));
}
function prepare(expectedInput,profilesInput,schemasInput,{check,need,started,deadline},contract=syntheticContract){
    need(contract===syntheticContract||contract===actualContract,'same_use_policy');
    const first=field(profilesInput,PROFILE,need),cap=field(field(first,'limits',need),'context_bytes',need);
    need(Number.isSafeInteger(cap)&&cap>0&&cap<=65536,'same_use_context');
    let ctx=textWindow.copy({descriptor:first,expected:expectedInput,profiles:Object.fromEntries([
        'private_compiler_entry_identity','private_compiler_entry_owner','compiler_library_inputs',
        'private_input_image_experiment','private_compiler_owned_view_basis'].map(k=>[k,field(profilesInput,k,need)])),
        schemas:schemasInput},'same_use_context',cap);
    const d=ctx.descriptor,e=ctx.expected,s=ctx.schemas.use,p=ctx.profiles,probe=p.private_compiler_entry_identity;
    need(Object.keys(ctx.schemas).sort().join(',')==='entry,identity,use','same_use_context');
    validate(d,s.$defs.policy,s,'same_use_context');validate(e,s.$defs.expected,s,'same_use_context');
    need(d.version===contract.version&&d.status===contract.status&&d.scope===contract.scope
        &&d.consumer_id===ID&&OFF.every(k=>d[k]===false),'same_use_policy');
    need(d.schema_sha256===hash(s)&&e.policy_sha256===hash(d)&&e.schema_sha256===hash(s)
        &&e.consumer_id===ID&&e.consumer_source_sha256===d.source_sha256
        &&d.identity_policy_sha256===hash(probe)&&d.identity_schema_sha256===hash(ctx.schemas.identity)
        &&d.entry_source_sha256===probe.source_sha256&&d.identity_source_sha256===probe.identity_source_sha256,'same_use_binding');
    const L=d.limits,R=probe.limits;
    need(Object.values(L).every(n=>Number.isSafeInteger(n)&&n>0)&&L.context_bytes<=R.context_bytes
        &&L.metadata_bytes<=R.metadata_bytes&&L.image_bytes<=R.image_bytes&&L.output_bytes<=2*R.output_bytes
        &&L.operation_ms<=R.operation_ms&&deadline<=started+L.operation_ms
        &&L.max_entry_bytes<=probe.scope_limits.entry_raw_bytes,'same_use_budget');
    check();
    let phase='preflight',attempts=0,returns=0,unknown=0,bounds=null,staged=null;
    const oldSchemas={identity:ctx.schemas.identity,entry:ctx.schemas.entry};
    function upper(n,schema,depth=0,key=''){
        need(depth<32,'same_use_bound');
        if(n.$ref)return upper(schema.$defs[n.$ref.replace('#/$defs/','')],schema,depth+1,key);
        if(Object.hasOwn(n,'const'))return bytes(n.const);
        if(n.enum)return Math.max(...n.enum.map(bytes));
        const kinds=Array.isArray(n.type)?n.type:[n.type];return Math.max(...kinds.map(type=>{
            if(type==='null')return 4;if(type==='boolean')return 5;if(type==='integer')return 16;
            if(type==='string'){
                if(n.pattern==='^[a-f0-9]{64}$')return 66;
                if(key==='invocation_id')return bytes(e.identity_expected.entry_expected.invocation_id);
                need(key==='relative_path','same_use_bound');return Math.max(...Object.values(e.identity_expected.roles).map(r=>bytes(r.relative_path)));
            }
            if(type==='array'){
                need(key==='records','same_use_bound');const count=Object.keys(p.private_compiler_owned_view_basis.role_expected_fields).length;
                need(count===5,'same_use_bound');return 2+count-1+count*upper(n.items,schema,depth+1);
            }
            need(type==='object'&&n.additionalProperties===false&&n.required.length===Object.keys(n.properties).length,'same_use_bound');
            return 2+Math.max(0,n.required.length-1)+Object.entries(n.properties).reduce((v,[k,row])=>v+bytes(k)+1+upper(row,schema,depth+1,k),0);
        }));
    }
    function price(identityContext,identityNamed){
        phase='pricing';check();need(e.identity_context_sha256===hash(identityContext),'same_use_binding');
        const q=e.identity_expected,E=q.roles[probe.entry_role].max_raw_bytes,C=bytes(identityContext),Cuse=bytes(ctx);
        need(q.entry_expected.entry_bytes<=E&&E<=L.max_entry_bytes,'same_use_budget');
        const M=bytes({expected:q.entry_expected,descriptor:p.private_compiler_entry_owner,capture:p.compiler_library_inputs,
            image:p.private_input_image_experiment,schema:ctx.schemas.entry});
        const names=[...Object.keys(p.private_compiler_owned_view_basis.role_expected_fields),...Object.keys(p.private_compiler_owned_view_basis.role_aliases)];
        const record=bytes({role:'x'.repeat(Math.max(...names.map(k=>k.length))),size_bytes:MAX,raw_sha256:'f'.repeat(64),relative_path_sha256:'f'.repeat(64)});
        const dimension=bytes({text_sha256:'f'.repeat(64),text_code_units:MAX});
        const rawMeta=Object.entries(q.roles).filter(([k])=>k!==probe.entry_role).reduce((n,[,r])=>n+r.max_raw_bytes,0);
        const session=M+rawMeta+5*record+dimension;
        const state=bytes({phase:'fixed_use_returned',attempts:MAX,returns:MAX,unknown:MAX,hash_live:true});
        const extra=7*Cuse+4*L.output_bytes+E+state;
        const metadata=7*C+4*probe.limits.output_bytes+session+7*Cuse+4*L.output_bytes+state;
        const join=identityNamed+M+extra,output=upper(s.$defs.result,s);
        need([session,extra,metadata,join,output].every(Number.isSafeInteger)&&Cuse<=L.context_bytes
            &&session<=p.private_input_image_experiment.limits.metadata_bytes
            &&metadata<=L.metadata_bytes&&join<=L.image_bytes&&output<=L.output_bytes,'same_use_budget');
        bounds={identity_named_with_input:identityNamed+M,use_context_bytes:Cuse,inner_input_bytes:M,session_metadata_upper:session,
            use_extra_named_bytes:extra,metadata_joined_upper:metadata,joined_named_upper:join,result_upper_bytes:output,max_consumer_utf8_bytes:E};
        phase='acquiring';check();
    }
    function consume(text,identity){
        need(bounds&&phase==='acquiring'&&attempts===0&&typeof text==='string','same_use_order');
        check();need(identity.ordered_acquisition_completed&&identity.entry_owner_allocated===false,'same_use_order');
        phase='fixed_use_running';attempts++;let handle=null;
        try{
            handle=crypto.createHash('sha256');const observed=handle.update(text,'utf8').digest('hex');returns++;check();
            const units=text.length,size=Buffer.byteLength(text,'utf8'),expected=e.identity_expected.entry_expected;
            need(typeof observed==='string'&&observed===expected.text_sha256&&observed===identity.entry.text_sha256
                &&size===expected.entry_bytes&&size===identity.entry.size_bytes&&units===identity.entry.text_code_units
                &&size<=bounds.max_consumer_utf8_bytes,'same_use_identity');
            phase='fixed_use_returned';staged={identity,use:{text_sha256:observed,text_code_units:units,utf8_bytes:size,attempts,returns}};
            check();return staged;
        }catch(error){if(returns===0)unknown++;throw error;}
        finally{handle=null;text=null;}
    }
    function finalize(value){
        // The acquisition factory has returned through its all-exit reference clear.
        check();need(value===staged&&phase==='fixed_use_returned'&&attempts===returns&&returns===1&&unknown===0,'same_use_order');
        phase='references_released';
        const result={version:contract.version,status:contract.status,invocation_id:e.identity_expected.entry_expected.invocation_id,
            expected_sha256:hash(e),policy_sha256:hash(d),schema_sha256:hash(s),consumer_id:ID,consumer_source_sha256:d.source_sha256,
            identity:value.identity,use:value.use,input_references_released:true,entry_owner_allocated:false,resource_bounds:bounds,
            full_native_cost:null,physical_heap_rss:null,flags:Object.fromEntries(FLAGS.map(k=>[k,false]))};
        validate(result,s.$defs.result,s,'same_use_output');need(bytes(result)<=L.output_bytes,'same_use_output');check();phase='result_validated';return result;
    }
    return {expected:e.identity_expected,profiles:p,schemas:oldSchemas,limit:L,
        price,consume,finalize,
        observation:()=>({phase,consumer_attempts:attempts,consumer_returns:returns,consumer_unknown:unknown}),
        dispose(){ctx=null;staged=null;bounds=null;}};
}
function note(error,c,operation){
    if(!error||!['object','function'].includes(typeof error))return;
    const use=operation?operation.observation():{phase:'preflight',consumer_attempts:0,consumer_returns:0,consumer_unknown:0};
    failures.set(error,{...use,identity_stage:c.primary_stage||c.stage,identity_terminal_stage:c.stage,publications:0,
        accounting:Object.fromEntries(['open_attempts','open_returns','read_attempts','read_returns','close_attempts','close_returns',
            'open_unknown','read_unknown','close_unknown','known_delivered_bytes','secondary_cleanup_errors'].map(k=>[k,c[k]])),
        raw_or_text_retained:false,entry_owner_allocated:false});
}
function failure(error){const r=failures.get(error);return r?{...r,accounting:{...r.accounting}}:null;}
module.exports={options,prepare,note,failure,syntheticContract,actualContract};

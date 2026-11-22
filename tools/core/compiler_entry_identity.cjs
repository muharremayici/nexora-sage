'use strict';
// Private metadata transaction, bound only by the fixed entry-owner facade.
// The binding seam is trusted first-party composition, not permission or a sandbox.
const path=require('node:path'),crypto=require('node:crypto'),{performance}=require('node:perf_hooks'),{types}=require('node:util');
const textWindow=require('./compiler_text_window.cjs');
const sameUse=require('./compiler_entry_same_read_use.cjs');
const {_canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate,StreamStagerRefusal}=require('./compiler_stream_stager.cjs');
const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
const hash=value=>sha(canonical(value)),bytes=value=>Buffer.byteLength(canonical(value),'utf8');
function _bindCompilerEntryIdentity(createEntrySourceOwner,CompilerEntryRefusal,samePath){
const entryIdentity=(()=>{
    const PROFILE='private_compiler_entry_identity',failures=new WeakMap();let active=null;
    const fields=Object.getOwnPropertyDescriptor,own=Object.hasOwn;
    const flagNames=['read_authority_granted','source_freshness_verified','physical_path_binding_verified','atomic_snapshot_verified',
        'compiler_loaded','input_code_evaluated','native_execution_attested','complete_compiler_inputs','snapshot_bound',
        'production_enabled','semantic_lineage','cache_eligible','publication_authority'];
    const refuse=code=>{throw new CompilerEntryRefusal(code);};
    const requireValue=(v,code)=>{if(!v)refuse(code);};
    function field(v,k){requireValue(v&&typeof v==='object'&&!types.isProxy(v),'identity_context');const d=fields(v,k);requireValue(d&&own(d,'value'),'identity_context');return d.value;}
    function checked(v,def,s){try{validate(v,s.$defs[def],s,'identity_context');}catch(e){if(e instanceof StreamStagerRefusal)refuse('identity_context');throw e;}}
    function observe(root,expectedInput,profilesInput,schemasInput,options){
        return transact(root,expectedInput,profilesInput,schemasInput,options,false);
    }
    function use(root,expectedInput,profilesInput,schemasInput,options){
        return transact(root,expectedInput,profilesInput,schemasInput,options,true);
    }
    function useActual(root,expectedInput,profilesInput,schemasInput,options){
        return transact(root,expectedInput,profilesInput,schemasInput,options,true,sameUse.actualContract);
    }
    function transact(root,expectedInput,profilesInput,schemasInput,options,withUse,contract=sameUse.syntheticContract){
        if(active){active.poisoned=true;refuse('identity_order');}
        requireValue(contract===sameUse.syntheticContract||contract===sameUse.actualContract,'identity_context');
        const c={stage:'preflight',poisoned:false,open_attempts:0,open_returns:0,read_attempts:0,read_returns:0,close_attempts:0,close_returns:0,
            open_unknown:0,read_unknown:0,close_unknown:0,known_delivered_bytes:0,secondary_cleanup_errors:0,records:[]};active=c;
        let ctx=null,result=null,failed=false,primary,useOperation=null;
        try{
            if(withUse)options=sameUse.options(options,requireValue);
            const now=options?.now||(()=>performance.now()),deadline=options?.deadline;requireValue(typeof now==='function','identity_deadline');
            const started=now();let last=started;
            const check=()=>{requireValue(active===c&&!c.poisoned,'identity_order');const t=now();requireValue(Number.isFinite(t)&&t>=last&&t<deadline,'identity_deadline');last=t;requireValue(active===c&&!c.poisoned,'identity_order');};
            requireValue(Number.isFinite(started)&&Number.isFinite(deadline)&&started<deadline,'identity_deadline');check();
            if(withUse){
                useOperation=sameUse.prepare(expectedInput,profilesInput,schemasInput,{check,need:requireValue,started,deadline},contract);
                expectedInput=useOperation.expected;profilesInput=useOperation.profiles;schemasInput=useOperation.schemas;
            }
            const first=field(profilesInput,PROFILE),limit=field(field(first,'limits'),'context_bytes');
            requireValue(Number.isSafeInteger(limit)&&limit>0&&limit<=65536,'identity_context');
            ctx=textWindow.copy({descriptor:first,expected:expectedInput,profiles:{private_compiler_entry_owner:field(profilesInput,'private_compiler_entry_owner'),
                compiler_library_inputs:field(profilesInput,'compiler_library_inputs'),private_input_image_experiment:field(profilesInput,'private_input_image_experiment'),
                private_compiler_owned_view_basis:field(profilesInput,'private_compiler_owned_view_basis')},schemas:schemasInput},'identity_context',limit);
            const d=ctx.descriptor,e=ctx.expected,s=ctx.schemas.identity,entry=ctx.profiles.private_compiler_entry_owner;
            requireValue(Object.keys(ctx.schemas).sort().join(',')==='entry,identity','identity_context');checked(d,'policy',s);checked(e,'expected',s);
            const C=bytes(ctx),O=d.limits.output_bytes,capture=ctx.profiles.compiler_library_inputs,image=ctx.profiles.private_input_image_experiment;
            requireValue(C<=d.limits.context_bytes&&deadline<=started+d.limits.operation_ms&&d.limits.operation_ms<=image.limits.capture_ms,'identity_budget');
            requireValue(d.version===contract.identityVersion&&d.scope===contract.identityScope
                &&d.status===contract.identityStatus
                &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>d[k]===false),'identity_context');
            requireValue(d.schema_sha256===hash(s)&&e.policy_sha256===hash(d)&&e.schema_sha256===d.schema_sha256
                &&d.entry_policy_sha256===hash(entry)&&d.entry_schema_sha256===hash(ctx.schemas.entry)
                &&d.capture_policy_sha256===hash(capture)&&d.image_policy_sha256===hash(image),'identity_binding');
            c.stage='role_binding';const roles=ctx.profiles.private_compiler_owned_view_basis.role_expected_fields;
            requireValue(hash(roles)===d.role_policy_sha256&&hash(roles)===hash(s.$defs.role_fields.const)&&d.entry_role==='installed_entry','identity_binding');
            requireValue(Object.keys(e.roles).length===Object.keys(roles).length&&Object.keys(roles).length===5
                &&Object.keys(roles).every(k=>own(e.roles,k)),'identity_scope');
            requireValue(typeof root==='string'&&path.isAbsolute(root)&&!root.split(/[\\/]/).includes('..'),'identity_scope');
            const rootText=value=>process.platform==='win32'?path.resolve(value).toLowerCase():path.resolve(value);
            requireValue(e.root_text.length<=1024&&e.root_text===rootText(root),'identity_scope');
            let rawTotal=0;const paths=new Set();
            for(const [role,key]of Object.entries(roles)){
                const r=e.roles[role],v=r.relative_path;
                requireValue(v.length<=256&&new RegExp(entry.relative_path_pattern,'u').test(v)&&!path.posix.isAbsolute(v)&&path.posix.normalize(v)===v
                    &&!v.split('/').includes('..')&&!paths.has(v),'identity_scope');paths.add(v);
                requireValue(r.expected_sha256===e.entry_expected[key]&&r.expectation_class==='historical_supplied','identity_binding');
                requireValue(r.max_raw_bytes<=(role===d.entry_role?d.scope_limits.entry_raw_bytes:d.scope_limits.metadata_raw_bytes)
                    &&r.max_raw_bytes<=(role===d.entry_role?capture.max_module_bytes:capture.max_file_bytes),'identity_budget');rawTotal+=r.max_raw_bytes;
            }
            requireValue(e.roles.provenance.relative_path===entry.provenance_relative_path&&rawTotal===e.max_total_raw_bytes
                &&rawTotal<=d.scope_limits.raw_bytes&&rawTotal<=capture.max_total_bytes
                &&e.max_delivered_bytes===rawTotal+5&&e.max_delivered_bytes<=d.scope_limits.delivered_bytes,'identity_budget');
            c.named=7*C+4*O+rawTotal+5+2*e.roles[d.entry_role].max_raw_bytes;
            requireValue(Number.isSafeInteger(c.named)&&c.named<=d.limits.image_bytes&&d.limits.image_bytes<=image.limits.image_bytes
                &&7*C+4*O<=d.limits.metadata_bytes&&d.limits.metadata_bytes<=image.limits.metadata_bytes,'identity_budget');check();
            const aliases=ctx.profiles.private_compiler_owned_view_basis.role_aliases;
            const roleOf=role=>own(aliases,role)?aliases[role]:role;
            requireValue(hash(aliases)===d.role_aliases_sha256,'identity_binding');
            let current=null,statTotal=0;
            const sidecar={
                beforePath(role,candidate){check();c.stage='path';current=roleOf(role);const r=e.roles[current];requireValue(r&&samePath(candidate,path.join(root,...r.relative_path.split('/'))),'identity_scope');},
                beforeOpen(role,member){check();c.stage='open';requireValue(current===roleOf(role)&&++c.open_attempts<=5,'identity_scope');},
                openReturned(){c.open_returns++;},openUnknown(){c.open_unknown++;},
                stat(role,size){check();c.stage='stat';requireValue(roleOf(role)===current&&size<=e.roles[current].max_raw_bytes,'identity_budget');statTotal+=size;requireValue(statTotal<=e.max_total_raw_bytes,'identity_budget');},
                readAttempt(){check();c.stage='read';c.read_attempts++;},readUnknown(){c.read_unknown++;},
                readFailed(){if(!c.primary_stage)c.primary_stage=c.stage;},
                readReturned(role,n){c.read_returns++;if(Number.isSafeInteger(n)&&n>=0)c.known_delivered_bytes+=n;else c.read_unknown++;
                    requireValue(c.known_delivered_bytes<=e.max_delivered_bytes,'identity_budget');},
                record(r){check();c.stage='record';const role=roleOf(r.role),declared=e.roles[role];
                    requireValue(!c.records.some(v=>v.role===role)&&r.raw_sha256===declared.expected_sha256,'identity_binding');
                    c.records.push({role,relative_path:declared.relative_path,declared_path_sha256:sha(declared.relative_path),member_path_sha256:r.relative_path_sha256,
                        size_bytes:r.size_bytes,raw_sha256:r.raw_sha256,expected_sha256:declared.expected_sha256,
                        expectation_class:declared.expectation_class,anchor_ref_sha256:declared.anchor_ref_sha256});},
                closeAttempt(){c.stage='close';c.close_attempts++;},closeReturned(){c.close_returns++;},
                closeUnknown(error,hadPrimary){c.close_unknown++;if(hadPrimary)c.secondary_cleanup_errors++;}
            };
            const op={sidecar,checkRoot(value){check();requireValue(e.root_text===rootText(value),'identity_scope');},
                finish(textHash,units,metrics,inputBytes){check();c.stage='settlement';
                    requireValue(c.records.length===5&&c.open_attempts===5&&c.open_returns===5&&c.close_returns===5
                        &&c.open_unknown===0&&c.read_unknown===0&&c.close_unknown===0,'identity_settlement');
                    requireValue(c.named+inputBytes<=d.limits.image_bytes,'identity_budget');
                    const receipt={version:d.version,status:d.status,invocation_id:e.entry_expected.invocation_id,expected_sha256:hash(e),policy_sha256:hash(d),schema_sha256:hash(s),
                        records:c.records.map(v=>({...v})),entry:{raw_sha256:c.records.find(v=>v.role===d.entry_role).raw_sha256,text_sha256:textHash,
                            size_bytes:e.entry_expected.entry_bytes,text_code_units:units},
                        accounting:Object.fromEntries(['open_attempts','open_returns','read_attempts','read_returns','close_attempts','close_returns','open_unknown','read_unknown','close_unknown','known_delivered_bytes','secondary_cleanup_errors'].map(k=>[k,c[k]])),
                        known_named_partial_bytes:c.named+inputBytes,full_native_cost:null,physical_heap_rss:null,
                        ordered_acquisition_completed:true,entry_owner_allocated:false,flags:Object.fromEntries(flagNames.map(k=>[k,false]))};
                    checked(receipt,'result',s);requireValue(bytes(receipt)<=O,'identity_output');check();return receipt;}};
            if(useOperation){useOperation.price(ctx,c.named);Object.defineProperty(op,'use',{value:(text,receipt)=>useOperation.consume(text,receipt)});}
            c.stage='acquisition';result=createEntrySourceOwner(root,e.entry_expected,ctx.profiles,ctx.schemas.entry,
                {now:()=>{check();return last;},deadline},false,null,null,null,op);
            if(useOperation)result=useOperation.finalize(result);
            c.stage='publication';check();requireValue(bytes(result)<=(useOperation?useOperation.limit.output_bytes:O),'identity_output');check();
        }catch(error){failed=true;primary=error;
            if(error&&['object','function'].includes(typeof error))failures.set(error,{stage:c.primary_stage||c.stage,terminal_stage:c.stage,publications:0,
                accounting:Object.fromEntries(['open_attempts','open_returns','read_attempts','read_returns','close_attempts','close_returns','open_unknown','read_unknown','close_unknown','known_delivered_bytes','secondary_cleanup_errors'].map(k=>[k,c[k]])),
                raw_or_text_retained:false,entry_owner_allocated:false});
            if(withUse){try{sameUse.note(error,c,useOperation);}catch{}}
        }finally{if(useOperation)useOperation.dispose();useOperation=null;c.records=null;ctx=null;if(active===c)active=null;}
        if(failed)throw primary;return result;
    }
    return {observe,use,useActual,useFailure:sameUse.failure,failure:error=>{const row=failures.get(error);return row?{...row,accounting:{...row.accounting}}:null;}};
})();
return entryIdentity;
}
module.exports={_bindCompilerEntryIdentity};

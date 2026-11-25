'use strict';
// Trusted first-party synthetic composition only; no evaluator or supplied events.
const path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {performance}=require('node:perf_hooks'),{types}=require('node:util');
const text=require('./compiler_text_window.cjs');
const {_canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate}=require('./compiler_stream_stager.cjs');
const PROFILE='private_compiler_entry_lifetime_trace';
const bytes=v=>Buffer.byteLength(canonical(v),'utf8');
const hash=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
const equalPath=(a,b)=>process.platform==='win32'?a.toLowerCase()===b.toLowerCase():a===b;
function _bindEntryLifetimeTrace(create,borrow,dispose,Refusal){
    let active=null;const failures=new WeakMap();
    const need=(v,code)=>{if(!v)throw new Refusal(code);};
    const checked=(value,definition,schema,code)=>{try{validate(value,definition,schema,code);}catch{throw new Refusal(code);}};
    const flat=(v,code)=>{
        need(v&&typeof v==='object'&&!types.isProxy(v)&&!Array.isArray(v),code);
        const p=Object.getOwnPropertyDescriptors(v);
        need(Reflect.ownKeys(p).every(k=>typeof k==='string'&&Object.hasOwn(p[k],'value')),code);
        return Object.fromEntries(Object.entries(p).map(([k,d])=>[k,d.value]));
    };
    function observe(root,expectedInput,profilesInput,schemasInput,optionsInput){
        if(active){active.poisoned=true;throw new Refusal('trace_reentry');}
        const c={events:[],incomplete:false,poisoned:false,owner_released:false,close_unknown:false,
            open_attempts:0,open_returns:0,close_returns:0,delivered_entry_bytes:0};
        active=c;let owner=null,primary=null,result=null,held=null;
        try{
            const options=flat(optionsInput,'trace_options');
            need(Object.keys(options).every(k=>k==='now'||k==='deadline'),'trace_options');
            const now=options.now||(()=>performance.now()),deadline=options.deadline;
            need(typeof now==='function','trace_deadline');
            let last=now();const started=last;
            const tick=()=>{
                const t=now();need(active===c&&!c.poisoned&&!c.incomplete,'trace_recording_incomplete');
                need(Number.isFinite(t)&&t>=last&&t<deadline,'trace_deadline');last=t;return t;
            };
            need(Number.isFinite(started)&&Number.isFinite(deadline)&&started<deadline,'trace_deadline');tick();
            try{held=text.copy({expected:expectedInput,profiles:profilesInput,schemas:schemasInput},'trace_context',65536);}
            catch{throw new Refusal('trace_context');}
            const {expected:e,profiles:p,schemas:s}=held,d=p[PROFILE],L=d?.limits;
            need(d&&L&&Object.keys(p).sort().join(',')===[PROFILE,'private_compiler_entry_owner','compiler_library_inputs','private_input_image_experiment'].sort().join(',')
                &&Object.keys(s).sort().join(',')==='entry,trace','trace_context');
            checked(d,s.trace.$defs.policy,s.trace,'trace_policy');checked(e,s.trace.$defs.expected,s.trace,'trace_expected');
            checked(e.entry_expected,s.entry.$defs.expected,s.entry,'trace_expected');
            need(d.version==='v1-private-synthetic-entry-owner-trace'&&d.scope==='temporary_synthetic_entry_owner_lifetime_only'
                &&d.result_status==='SYNTHETIC_ENTRY_OWNER_TRACE_COMPLETED_NOT_COMPILER_ADMITTED'
                &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>d[k]===false),'trace_policy');
            need(Object.values(L).every(v=>Number.isSafeInteger(v)&&v>0)&&L.context_bytes<=65536&&L.events<=128
                &&L.metadata_bytes<=p.private_input_image_experiment.limits.metadata_bytes
                &&L.image_bytes<=p.private_input_image_experiment.limits.image_bytes
                &&L.operation_ms<=p.private_input_image_experiment.limits.capture_ms&&deadline<=started+L.operation_ms,'trace_budget');
            need(d.schema_sha256===hash(s.trace)&&d.owner_schema_sha256===hash(s.entry)
                &&d.owner_policy_sha256===hash(p.private_compiler_entry_owner)
                &&e.policy_sha256===hash(d)&&e.schema_sha256===hash(s.trace)&&e.owner_source_sha256===d.owner_source_sha256
                &&e.entry_expected.compiler_version===d.synthetic_version,'trace_identity');
            need(typeof root==='string'&&path.isAbsolute(root)&&root===path.resolve(root),'trace_scope');
            const boundary=path.dirname(root),relative=path.relative(path.resolve(os.tmpdir()),boundary);
            need(path.basename(root)===d.root_member&&path.basename(boundary).startsWith(d.boundary_prefix)
                &&relative!==''&&relative!=='..'&&!relative.startsWith('..'+path.sep)&&!path.isAbsolute(relative),'trace_scope');
            const longest=s.trace.$defs.event.properties.kind.enum.reduce((a,b)=>a.length>=b.length?a:b);
            const eventUpper=bytes({sequence:L.events,kind:longest,role:'provenance',first:Number.MAX_SAFE_INTEGER,second:Number.MAX_SAFE_INTEGER});
            const C=bytes(held),E=2+L.events*(eventUpper+1),metadata=8*C+4*E+4*L.output_bytes;
            const raw=5*(L.role_bytes+1),known=metadata+C+3*raw;
            need(Number.isSafeInteger(known)&&C<=L.context_bytes&&metadata+C<=L.metadata_bytes&&known<=L.image_bytes
                &&e.entry_expected.entry_bytes<=L.role_bytes,'trace_budget');tick();
            const note=(kind,role=null,first=null,second=null)=>{
                if(c.incomplete||c.events.length>=L.events){c.incomplete=true;throw new Refusal('trace_event_budget');}
                need([first,second].every(v=>v===null||Number.isSafeInteger(v)&&v>=0),'trace_event_shape');
                const row={sequence:c.events.length,kind,role,first,second};
                validate(row,s.trace.$defs.event,s.trace,'trace_event_shape');c.events.push(row);
            };
            const safe=(...args)=>{try{note(...args);}catch{c.incomplete=true;}};
            const trace={reservation:metadata,note,safe,
                rootResolved(value){need(equalPath(value,root),'trace_scope');},
                packageMarker(value){need(value===d.role_paths.package,'trace_scope');},
                packageResolved(value){need(equalPath(value,path.dirname(path.join(root,...d.role_paths.package.split('/')))),'trace_scope');},
                path(role,value){tick();need(Object.hasOwn(d.role_paths,role)&&equalPath(value,path.join(root,...d.role_paths[role].split('/'))),'trace_scope');},
                stat(role,size){need(Number.isSafeInteger(size)&&size>=0&&size<=L.role_bytes,'trace_budget');},
                io(kind,role){
                    if(kind==='open_attempt')c.open_attempts++;
                    if(kind==='open_return')c.open_returns++;
                    if(kind==='close_return')c.close_returns++;
                    if(kind==='close_unknown')c.close_unknown=true;
                    if(role==='entry')safe(kind,role);
                },
                released(state){if(!c.owner_released){c.owner_released=true;safe(state==='ABORTED'?'owner_abort':'owner_release','entry',0,0);}}
            };
            note('transaction_begin');
            const ownerProfiles=Object.fromEntries(['private_compiler_entry_owner','compiler_library_inputs','private_input_image_experiment'].map(k=>[k,p[k]]));
            // The private extra argument is reachable only through this source-fixed binder.
            owner=create(root,e.entry_expected,ownerProfiles,s.entry,{now:tick,deadline},false,null,null,null,null,trace);tick();
            const expected={context_sha256:hash(e.entry_expected),...Object.fromEntries(['entry_sha256','text_sha256','policy_sha256','schema_sha256'].map(k=>[k,e.entry_expected[k]]))};
            borrow(owner,expected,row=>{
                tick();note('text_use','entry',row.size_bytes,row.text.length);tick();return undefined;
            });
            tick();need(c.owner_released&&c.open_attempts===5&&c.open_returns===5&&c.close_returns===5&&!c.close_unknown,'trace_settlement');
            note('owner_scope_complete');tick();
            const events=c.events.map(row=>({...row}));
            c.delivered_entry_bytes=events.filter(row=>row.kind==='read_return').reduce((n,row)=>n+(row.first||0),0);
            result={version:d.version,status:d.result_status,scope:d.scope,expected_sha256:hash(e),
                events,entry_sha256:e.entry_expected.entry_sha256,text_sha256:e.entry_expected.text_sha256,
                delivered_entry_bytes:c.delivered_entry_bytes,role_opens:c.open_returns,role_closes:c.close_returns,
                metadata_reserved_bytes:metadata,known_named_inputs_reserved_bytes:known,
                owner_scope_complete:true,recording_incomplete:false,owner_released:true,
                compiled_retention_bytes:null,physical_fit:null,transaction_settled:false,
                flags:Object.fromEntries(d.denied_flags.map(k=>[k,false]))};
            validate(result,s.trace.$defs.result,s.trace,'trace_result');need(bytes(result)<=L.output_bytes,'trace_output_budget');
            const freeze=v=>{if(v&&typeof v==='object'){for(const x of Object.values(v))freeze(x);Object.freeze(v);}};
            freeze(result);tick();
        }catch(error){primary=error;}
        finally{
            try{dispose(owner);}catch(error){c.incomplete=true;if(!primary)primary=error;}
            owner=held=null;
            if(active===c)active=null;
        }
        if(primary){
            if(primary&&(typeof primary==='object'||typeof primary==='function'))failures.set(primary,{
                status:'INCOMPLETE_SYNTHETIC_OWNER_TRACE_NOT_SUCCESS',events:c.events.map(row=>({...row})),
                recording_incomplete:c.incomplete,owner_released:c.owner_released,close_unknown:c.close_unknown,
                role_opens:c.open_returns,role_closes:c.close_returns,owner_scope_complete:false,transaction_settled:false,
                compiler_loaded:false,physical_fit:null});
            throw primary;
        }
        return result;
    }
    return {observe,failure(error){const value=failures.get(error);return value?structuredClone(value):null;}};
}
module.exports={_bindEntryLifetimeTrace};

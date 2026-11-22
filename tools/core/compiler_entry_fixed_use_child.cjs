'use strict';
// Shared protocol only; fixed source entrypoints choose schemas/core/root gate.
const synthetic=Object.freeze({child:'v1-fixed-synthetic-entry-same-read-use',actual:false,
    refused:'SYNTHETIC_FIXED_USE_REFUSED',completed:'SYNTHETIC_FIXED_USE_COMPLETED'});
const historical=Object.freeze({child:'v1-fixed-historical-five-role-entry-use',actual:true,
    refused:'HISTORICAL_FIVE_ROLE_FIXED_USE_REFUSED',completed:'HISTORICAL_FIVE_ROLE_FIXED_USE_COMPLETED'});
function runFixed(started,S,PS,ES,US,contract){
// Authored-temporary identity observation only. No evaluator/owner is returned.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const Module=require('node:module'),vm=require('node:vm'),{performance}=require('node:perf_hooks');
const cap=Number(process.argv[2]),outputCap=Number(process.argv[3]),remaining=Number(process.argv[4]);
const deadline=started+remaining,DS=S;
const owner=require('./compiler_entry_owner.cjs');
const {installFiveRoleGuard,installActualFiveRoleGuard,settleFiveRoleGuard}=require('./compiler_five_role_io_guard.cjs');
const { _canonicalCompilerStreamMetadata:canonical, _validateCompilerStreamValue:validate }=require('./compiler_stream_stager.cjs');
const native=Object.fromEntries(['openSync','readSync','writeSync','closeSync','realpathSync','lstatSync','statSync','fstatSync'].map(k=>[k,fs[k]]));
const ID=contract.child,PROBE='private_compiler_entry_identity';
const hash=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
const bytes=v=>Buffer.byteLength(canonical(v),'utf8');
const norm=v=>process.platform==='win32'?path.resolve(v).toLowerCase():path.resolve(v);
const fail=code=>{const e=new Error(code);e.code=code;throw e;};
const need=(v,code)=>{if(!v)fail(code);};
const inside=(a,b)=>{const r=path.relative(a,b);return r!==''&&r!=='..'&&!r.startsWith('..'+path.sep)&&!path.isAbsolute(r);};
let q=null,stage='stdin',steps=0,work=0,armed=false,forbidden=0,buffer=null,raw=null;
const fds=new Map(),roles=[],meta={attempts:0,returns:0,unknown:0},a={open_attempts:0,open_returns:0,open_unknown:0,read_attempts:0,
    read_unknown:0,close_attempts:0,close_returns:0,close_unknown:0,known_delivered_bytes:0,known_open_descriptors:null,emergency_closes:0,roles};
const out={version:ID,status:contract.refused,request_binding_sha256:null,transport_binding_sha256:null,
    probe_context_sha256:null,observation:null,error:null,payload_accounting:a,metadata_accounting:meta,forbidden_attempts:0,wrapper_steps:0};
function check(){
    need(Number.isFinite(deadline)&&performance.now()<deadline,'reader_deadline');
    steps++;need(!work||steps<=work,'reader_work');
}
function phase(v){stage=v;check();}
function deny(){forbidden++;fail('reader_scope');}
function upper(n,schema,e,key='',depth=0){
    need(depth<32,'reader_budget');if(n.$ref)return upper(schema.$defs[n.$ref.replace('#/$defs/','')],schema,e,key,depth+1);
    if(Object.hasOwn(n,'const'))return bytes(n.const);if(n.enum)return Math.max(...n.enum.map(bytes));
    return Math.max(...(Array.isArray(n.type)?n.type:[n.type]).map(t=>{
        if(t==='null')return 4;if(t==='boolean')return 5;if(t==='integer')return 16;
        if(t==='string'){if(n.pattern==='^[a-f0-9]{64}$')return 66;if(key==='invocation_id')return bytes(e.entry_expected.invocation_id);
            need(key==='relative_path','reader_budget');return Math.max(...Object.values(e.roles).map(r=>bytes(r.relative_path)));}
        if(t==='array'){need(key==='records'&&Object.keys(e.roles).length===5,'reader_budget');return 6+5*upper(n.items,schema,e,'',depth+1);}
        need(t==='object'&&n.additionalProperties===false&&n.required.length===Object.keys(n.properties).length,'reader_budget');
        return 2+Math.max(0,n.required.length-1)+Object.entries(n.properties).reduce((v,[k,row])=>v+bytes(k)+1+upper(row,schema,e,k,depth+1),0);
    }));
}
try{
    const ceilings=S.$defs.ceilings.const;
    need(process.argv.length===5&&Number.isSafeInteger(cap)&&cap>0&&cap<=ceilings.input_bytes
        &&Number.isSafeInteger(outputCap)&&outputCap>0&&outputCap<=ceilings.output_bytes
        &&Number.isSafeInteger(remaining)&&remaining>0&&remaining<=ceilings.operation_ms,'reader_bounds');
    phase('stdin');buffer=Buffer.alloc(cap+1);let count=0;
    while(count<buffer.length){check();const n=native.readSync(0,buffer,count,buffer.length-count,null);
        need(Number.isSafeInteger(n)&&n>=0&&n<=buffer.length-count,'reader_stdin');if(n===0)break;count+=n;}
    need(count<=cap,'reader_stdin');raw=buffer.subarray(0,count);buffer=null;
    phase('decode');q=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw));
    need(Buffer.from(canonical(q)).equals(raw),'reader_canonical');raw=null;
    validate(q,S.$defs.child_request,S,'reader_shape');validate(q.request,DS.$defs.request,DS,'reader_shape');
    validate(q.expected,DS.$defs.dispatch_expected,DS,'reader_shape');
    const p=q.probe.profiles,u=q.probe.expected,e=u.identity_expected,d=p[PROBE],entry=p.private_compiler_entry_owner;
    validate(u,US.$defs.expected,US,'reader_shape');validate(p.private_compiler_entry_same_read_use,US.$defs.policy,US,'reader_shape');
    validate(d,PS.$defs.policy,PS,'reader_shape');validate(e,PS.$defs.expected,PS,'reader_shape');
    validate(entry,ES.$defs.policy,ES,'reader_shape');validate(e.entry_expected,ES.$defs.expected,ES,'reader_shape');
    const bounds=DS.$defs.dispatch_policy.properties.declared_scope_limits.const,names=Object.keys(p.private_compiler_owned_view_basis.role_expected_fields);
    work=cap+1+d.scope_limits.delivered_bytes+outputCap+2*bounds.root_chars+11*bounds.path_chars+12+8*5+24;
    need(q.version===ID&&q.transport_expected.child_id===ID&&q.operation_ms<=ceilings.operation_ms&&remaining<=q.operation_ms
        &&q.wrapper_steps===work&&q.limits.wrapper_steps===work&&q.limits.child_input_bytes===cap&&q.limits.child_output_bytes===outputCap
        &&q.limits.metadata_bytes<=ceilings.metadata_bytes&&q.limits.image_bytes<=ceilings.image_bytes,
        'reader_bounds');
    phase('binding');
    const profiles=Object.fromEntries(Object.entries(p).filter(([k])=>k!==PROBE&&k!=='private_compiler_entry_same_read_use'));
    const context={descriptor:d,expected:e,profiles,schemas:{identity:PS,entry:ES}};
    const useContext={descriptor:p.private_compiler_entry_same_read_use,expected:u,profiles:{[PROBE]:d,...profiles},schemas:{identity:PS,entry:ES,use:US}};
    need(hash(S)===q.transport_expected.schema_sha256&&hash(DS)===q.expected.schema_sha256
        &&hash(PS)===q.probe.schema_refs.probe&&hash(ES)===q.probe.schema_refs.entry&&hash(US)===q.probe.schema_refs.use
        &&hash(useContext)===q.expected.use_context_sha256&&hash(u)===q.expected.use_expected_sha256
        &&u.identity_context_sha256===hash(context)&&q.transport_expected.use_context_sha256===hash(useContext)
        &&q.request.use_context_sha256===hash(useContext)&&q.request.use_expected_sha256===hash(u)
        &&hash(context)===q.expected.probe_context_sha256&&hash(e)===q.expected.probe_expected_sha256
        &&hash(q.request)===q.request_binding_sha256
        &&hash(q.expected)===q.transport_expected.dispatch_expected_sha256
        &&q.transport_expected.dispatch_policy_sha256===q.expected.policy_sha256
        &&q.transport_expected.probe_context_sha256===q.expected.probe_context_sha256
        &&(!contract.actual||(q.request.target_metadata_scope_sha256===hash(DS.$defs.metadata_scope.const)
            &&q.expected.target_metadata_scope_sha256===hash(DS.$defs.metadata_scope.const)
            &&q.external_authority_sha256===q.transport_expected.external_authority_sha256))
        &&q.request.purpose===DS.$defs.dispatch_policy.properties.scope.const
        &&q.request.root_scheme===DS.$defs.dispatch_policy.properties.target_root_scheme.const
        &&q.request.invocation_id===e.entry_expected.invocation_id
        &&q.request.max_open_attempts===5&&q.request.unassigned_headroom_bytes===0
        &&q.request.max_total_raw_bytes===e.max_total_raw_bytes&&q.request.max_delivered_bytes===e.max_delivered_bytes
        &&q.transport_expected.probe_context_sha256===q.expected.probe_context_sha256
        &&hash({transport_expected:q.transport_expected,request_binding_sha256:q.request_binding_sha256})===q.transport_binding_sha256,
        'reader_binding');
    out.request_binding_sha256=q.request_binding_sha256;out.transport_binding_sha256=q.transport_binding_sha256;
    out.probe_context_sha256=q.expected.probe_context_sha256;
    const C=bytes(context),M=bytes({expected:e.entry_expected,descriptor:entry,capture:p.compiler_library_inputs,image:p.private_input_image_experiment,schema:ES});
    const fieldNames=[...names,...Object.keys(p.private_compiler_owned_view_basis.role_aliases)];
    const record=bytes({role:'x'.repeat(Math.max(...fieldNames.map(k=>k.length))),size_bytes:Number.MAX_SAFE_INTEGER,raw_sha256:'a'.repeat(64),relative_path_sha256:'a'.repeat(64)});
    const dimension=bytes({text_sha256:'a'.repeat(64),text_code_units:Number.MAX_SAFE_INTEGER});
    const rawMetadata=names.filter(k=>k!==d.entry_role).reduce((n,k)=>n+e.roles[k].max_raw_bytes,0);
    const probeReserve=7*C+4*d.limits.output_bytes+e.max_delivered_bytes+2*e.roles[d.entry_role].max_raw_bytes+M;
    const session=M+rawMetadata+5*record+dimension,L=p.private_compiler_entry_same_read_use.limits;
    const state=bytes({phase:'fixed_use_returned',attempts:Number.MAX_SAFE_INTEGER,returns:Number.MAX_SAFE_INTEGER,unknown:Number.MAX_SAFE_INTEGER,hash_live:true});
    const Cu=bytes(useContext),E=e.roles[d.entry_role].max_raw_bytes,extra=7*Cu+4*L.output_bytes+E+state;
    const core={identity_named_with_input:probeReserve,use_context_bytes:Cu,inner_input_bytes:M,session_metadata_upper:session,
        use_extra_named_bytes:extra,metadata_joined_upper:7*C+4*d.limits.output_bytes+session+7*Cu+4*L.output_bytes+state,
        joined_named_upper:probeReserve+extra,result_upper_bytes:upper(US.$defs.result,US,e),max_consumer_utf8_bytes:E};
    need(hash(core)===hash(q.core_bounds)&&Cu<=L.context_bytes&&C<=d.limits.context_bytes
        &&core.metadata_joined_upper<=L.metadata_bytes&&core.joined_named_upper<=L.image_bytes
        &&core.result_upper_bytes<=L.output_bytes&&session<=p.private_input_image_experiment.limits.metadata_bytes,'reader_budget');
    const installGuard=contract===historical?installActualFiveRoleGuard:installFiveRoleGuard;
    installGuard(q,e,names,check,need,fail,deny,a,meta,roles,fds,native);
    armed=true;out.request_binding_sha256=q.request_binding_sha256;out.transport_binding_sha256=q.transport_binding_sha256;
    out.probe_context_sha256=q.expected.probe_context_sha256;
    phase('acquisition');const observe=contract===historical?owner.observeHistoricalCompilerEntryUse:owner.observeSyntheticCompilerEntryUse;
    const result=observe(q.probe.root,u,p,{identity:PS,entry:ES,use:US},
        {now:()=>performance.now(),deadline});
    phase('result');validate(result,US.$defs.result,US,'reader_output');need(hash(result.resource_bounds)===hash(core),'reader_budget');
    need(a.open_attempts===5&&a.open_returns===5&&a.close_returns===5&&fds.size===0
        &&!a.open_unknown&&!a.read_unknown&&!a.close_unknown&&!forbidden,'reader_settlement');
    out.observation=result;out.status=contract.completed;
}catch(error){
    let probeFailure=null,useFailure=null;try{probeFailure=owner.compilerEntryIdentityFailureObservation(error);useFailure=owner.compilerEntryUseFailureObservation(error);}catch{}
    if(useFailure){try{validate(useFailure,{...S.$defs.use_failure,type:'object'},S,'reader_output');}catch{useFailure=null;}}
    const codes=S.$defs.refusal_code.enum;
    out.error={code:codes.includes(error?.code)?error.code:'reader_child_error',stage,probe_failure:probeFailure,use_failure:useFailure};out.observation=null;
}finally{
    settleFiveRoleGuard(a,fds,native);
    if(a.emergency_closes){out.status=contract.refused;out.observation=null;
        out.error??={code:'reader_emergency',stage:'cleanup',probe_failure:null,use_failure:null};}
    a.known_open_descriptors=a.open_unknown||a.close_unknown?null:fds.size;
    out.forbidden_attempts=forbidden;q=raw=buffer=null;
}
try{
    phase('publication');out.wrapper_steps=steps;
    need(roles.length<=S.$defs.payload_role_count.const,'reader_output');
    if(out.error!==null){
        validate(out.error,{...S.$defs.node_result.properties.error,type:'object'},S,'reader_output');
        if(out.error.probe_failure!==null)validate(out.error.probe_failure,{...S.$defs.probe_failure,type:'object'},S,'reader_output');
        if(out.error.use_failure!==null)validate(out.error.use_failure,{...S.$defs.use_failure,type:'object'},S,'reader_output');
    }
    validate(out,S.$defs.node_result,S,'reader_output');
    const line=Buffer.from(JSON.stringify(out)+'\n');need(line.length<=outputCap,'reader_output_budget');
    let offset=0;while(offset<line.length){check();const n=native.writeSync(1,line,offset,line.length-offset);
        need(Number.isSafeInteger(n)&&n>0&&n<=line.length-offset,'reader_stdout');offset+=n;}
}catch{process.exitCode=1;}

}
function runSynthetic(started,S,PS,ES,US){return runFixed(started,S,PS,ES,US,synthetic);}
function runHistorical(started,S,PS,ES,US){return runFixed(started,S,PS,ES,US,historical);}
module.exports={runSynthetic,runHistorical};

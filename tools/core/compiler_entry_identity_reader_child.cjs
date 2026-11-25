'use strict';
// Authored-temporary identity observation only. No evaluator/owner is returned.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const Module=require('node:module'),vm=require('node:vm'),{performance}=require('node:perf_hooks');
const started=performance.now(),cap=Number(process.argv[2]),outputCap=Number(process.argv[3]),remaining=Number(process.argv[4]);
const deadline=started+remaining;
const S=require('../../config/schemas/compiler_entry_identity_reader.schema.json');
const DS=require('../../config/schemas/compiler_entry_identity_dispatch.schema.json');
const PS=require('../../config/schemas/compiler_entry_identity.schema.json');
const ES=require('../../config/schemas/compiler_entry_owner.schema.json');
const owner=require('./compiler_entry_owner.cjs');
const {installFiveRoleGuard,settleFiveRoleGuard}=require('./compiler_five_role_io_guard.cjs');
const { _canonicalCompilerStreamMetadata:canonical, _validateCompilerStreamValue:validate }=require('./compiler_stream_stager.cjs');
const native=Object.fromEntries(['openSync','readSync','writeSync','closeSync','realpathSync','lstatSync','statSync','fstatSync'].map(k=>[k,fs[k]]));
const ID='v1-fixed-synthetic-entry-identity-reader',PROBE='private_compiler_entry_identity';
const hash=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
const bytes=v=>Buffer.byteLength(canonical(v),'utf8');
const norm=v=>process.platform==='win32'?path.resolve(v).toLowerCase():path.resolve(v);
const fail=code=>{const e=new Error(code);e.code=code;throw e;};
const need=(v,code)=>{if(!v)fail(code);};
const inside=(a,b)=>{const r=path.relative(a,b);return r!==''&&r!=='..'&&!r.startsWith('..'+path.sep)&&!path.isAbsolute(r);};
let q=null,stage='stdin',steps=0,work=0,armed=false,forbidden=0,buffer=null,raw=null;
const fds=new Map(),roles=[],meta={attempts:0,returns:0,unknown:0},a={open_attempts:0,open_returns:0,open_unknown:0,read_attempts:0,
    read_unknown:0,close_attempts:0,close_returns:0,close_unknown:0,known_delivered_bytes:0,known_open_descriptors:null,emergency_closes:0,roles};
const out={version:ID,status:'SYNTHETIC_FIVE_ROLE_READER_REFUSED',request_binding_sha256:null,transport_binding_sha256:null,
    probe_context_sha256:null,observation:null,error:null,payload_accounting:a,metadata_accounting:meta,forbidden_attempts:0,wrapper_steps:0};
function check(){
    need(Number.isFinite(deadline)&&performance.now()<deadline,'reader_deadline');
    steps++;need(!work||steps<=work,'reader_work');
}
function phase(v){stage=v;check();}
function deny(){forbidden++;fail('reader_scope');}
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
    validate(q.expected,DS.$defs.expected,DS,'reader_shape');
    const p=q.probe.profiles,e=q.probe.expected,d=p[PROBE],entry=p.private_compiler_entry_owner;
    validate(d,PS.$defs.policy,PS,'reader_shape');validate(e,PS.$defs.expected,PS,'reader_shape');
    validate(entry,ES.$defs.policy,ES,'reader_shape');validate(e.entry_expected,ES.$defs.expected,ES,'reader_shape');
    const bounds=DS.$defs.policy.properties.declared_scope_limits.const,names=Object.keys(p.private_compiler_owned_view_basis.role_expected_fields);
    work=cap+1+d.scope_limits.delivered_bytes+outputCap+2*bounds.root_chars+11*bounds.path_chars+12+8*5+16;
    need(q.version===ID&&q.transport_expected.child_id===ID&&q.operation_ms<=ceilings.operation_ms&&remaining<=q.operation_ms
        &&q.wrapper_steps===work&&q.limits.wrapper_steps===work&&q.limits.child_input_bytes===cap&&q.limits.child_output_bytes===outputCap
        &&q.limits.metadata_bytes<=ceilings.metadata_bytes&&q.limits.image_bytes<=ceilings.image_bytes,
        'reader_bounds');
    phase('binding');
    const profiles=Object.fromEntries(Object.entries(p).filter(([k])=>k!==PROBE));
    const context={descriptor:d,expected:e,profiles,schemas:{identity:PS,entry:ES}};
    need(hash(S)===q.transport_expected.schema_sha256&&hash(DS)===q.expected.schema_sha256
        &&hash(PS)===q.probe.schema_refs.probe&&hash(ES)===q.probe.schema_refs.entry
        &&hash(context)===q.expected.probe_context_sha256&&hash(e)===q.expected.probe_expected_sha256
        &&hash(q.request)===q.request_binding_sha256
        &&hash(q.expected)===q.transport_expected.dispatch_expected_sha256
        &&q.transport_expected.dispatch_policy_sha256===q.expected.policy_sha256
        &&q.transport_expected.probe_context_sha256===q.expected.probe_context_sha256
        &&q.request.purpose===DS.$defs.policy.properties.scope.const
        &&q.request.root_scheme===DS.$defs.policy.properties.target_root_scheme.const
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
    need(C<=d.limits.context_bytes&&M+rawMetadata+5*record+dimension<=p.private_input_image_experiment.limits.metadata_bytes
        &&probeReserve+5*(cap+1)+4*outputCap+32*(bounds.root_chars+bounds.path_chars+1)<=q.limits.image_bytes,'reader_budget');
    installFiveRoleGuard(q,e,names,check,need,fail,deny,a,meta,roles,fds,native);
    armed=true;out.request_binding_sha256=q.request_binding_sha256;out.transport_binding_sha256=q.transport_binding_sha256;
    out.probe_context_sha256=q.expected.probe_context_sha256;
    phase('acquisition');const result=owner.observeSyntheticCompilerEntryIdentity(q.probe.root,e,p,{identity:PS,entry:ES},
        {now:()=>performance.now(),deadline});
    phase('result');validate(result,PS.$defs.result,PS,'reader_output');
    need(a.open_attempts===5&&a.open_returns===5&&a.close_returns===5&&fds.size===0
        &&!a.open_unknown&&!a.read_unknown&&!a.close_unknown&&!forbidden,'reader_settlement');
    out.observation=result;out.status='SYNTHETIC_FIVE_ROLE_READER_COMPLETED';
}catch(error){
    let probeFailure=null;try{probeFailure=owner.compilerEntryIdentityFailureObservation(error);}catch{}
    const codes=S.$defs.refusal_code.enum;
    out.error={code:codes.includes(error?.code)?error.code:'reader_child_error',stage,probe_failure:probeFailure};out.observation=null;
}finally{
    settleFiveRoleGuard(a,fds,native);
    if(a.emergency_closes){out.status='SYNTHETIC_FIVE_ROLE_READER_REFUSED';out.observation=null;
        out.error??={code:'reader_emergency',stage:'cleanup',probe_failure:null};}
    a.known_open_descriptors=a.open_unknown||a.close_unknown?null:fds.size;
    out.forbidden_attempts=forbidden;q=raw=buffer=null;
}
try{
    phase('publication');out.wrapper_steps=steps;
    need(roles.length<=S.$defs.payload_role_count.const,'reader_output');
    if(out.error!==null){
        validate(out.error,{...S.$defs.node_result.properties.error,type:'object'},S,'reader_output');
        if(out.error.probe_failure!==null)validate(out.error.probe_failure,{...S.$defs.probe_failure,type:'object'},S,'reader_output');
    }
    validate(out,S.$defs.node_result,S,'reader_output');
    const line=Buffer.from(JSON.stringify(out)+'\n');need(line.length<=outputCap,'reader_output_budget');
    let offset=0;while(offset<line.length){check();const n=native.writeSync(1,line,offset,line.length-offset);
        need(Number.isSafeInteger(n)&&n>0&&n<=line.length-offset,'reader_stdout');offset+=n;}
}catch{process.exitCode=1;}

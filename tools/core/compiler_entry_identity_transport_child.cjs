'use strict';
// Fixed nonreading metadata endpoint. First-party loader I/O is outside target
// payload accounting; only stdin/stdout descriptors are used by this operation.
const fs=require('node:fs'),crypto=require('node:crypto'),Module=require('node:module');
const {performance}=require('node:perf_hooks');
const started=performance.now();
const cap=Number(process.argv[2]),outputCap=Number(process.argv[3]),remaining=Number(process.argv[4]);
const deadline=started+remaining;
const schema=require('../../config/schemas/compiler_entry_identity_transport.schema.json');
const dispatchSchema=require('../../config/schemas/compiler_entry_identity_dispatch.schema.json');
const { _canonicalCompilerStreamMetadata:canonical, _validateCompilerStreamValue:validate }=require('./compiler_stream_stager.cjs');
const read=fs.readSync,write=fs.writeSync,ceilings=schema.$defs.wire_ceilings.const;
const hash=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
const fail=code=>{const e=new Error(code);e.code=code;throw e;};
const need=(v,code)=>{if(!v)fail(code);};
const ID='v1-fixed-nonreading-entry-identity-child';
let request=null,raw=null,buffer=null,stage='stdin',steps=0,payloadAttempts=0;
// No filesystem/loader path may be selected by received metadata.
for(const name of ['openSync','open','readFileSync','readFile','statSync','lstatSync','realpathSync','fstatSync',
    'readdirSync','readdir','writeFileSync','writeFile','appendFileSync','closeSync'])
    fs[name]=()=>{payloadAttempts++;fail('transport_payload_api');};
Module._load=()=>fail('transport_late_module');
const fields=schema.$defs.node_result.properties.flags.required;
const out={version:ID,status:'SYNTHETIC_FIVE_ROLE_METADATA_REFUSED',request_binding_sha256:null,expected_sha256:null,
    transport_binding_sha256:null,probe_context_sha256:null,identities:null,payload_open_attempts:0,error:null,
    wrapper_steps:0,flags:Object.fromEntries(fields.map(k=>[k,false]))};
function check(){
    need(Number.isFinite(deadline)&&performance.now()<deadline,'transport_child_deadline');
    steps++;
    need(steps<=cap+1+5+6,'transport_child_work');
}
function phase(name){stage=name;check();}
try{
    need(process.argv.length===5&&Number.isSafeInteger(cap)&&cap>0&&cap<=ceilings.input_bytes
        &&Number.isSafeInteger(outputCap)&&outputCap>0&&outputCap<=ceilings.output_bytes
        &&Number.isSafeInteger(remaining)&&remaining>0&&remaining<=ceilings.operation_ms,'transport_child_bounds');
    phase('stdin');buffer=Buffer.alloc(cap+1);let count=0;
    while(count<buffer.length){
        check();const n=read(0,buffer,count,buffer.length-count,null);
        need(Number.isSafeInteger(n)&&n>=0&&n<=buffer.length-count,'transport_stdin');
        if(n===0)break;count+=n;
    }
    need(count<=cap,'transport_stdin_budget');raw=buffer.subarray(0,count);buffer=null;
    phase('decode');request=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw));
    need(Buffer.from(canonical(request),'utf8').equals(raw),'transport_canonical');raw=null;
    phase('shape');validate(request,schema.$defs.child_request,schema,'transport_shape');
    validate(request.request,dispatchSchema.$defs.request,dispatchSchema,'transport_shape');
    validate(request.expected,dispatchSchema.$defs.expected,dispatchSchema,'transport_shape');
    const q=request,r=q.request,e=q.expected,t=q.transport_expected;
    need(q.version===ID&&t.child_id===ID&&q.operation_ms<=ceilings.operation_ms&&remaining<=q.operation_ms
        &&q.wrapper_steps===cap+1+5+6,'transport_child_bounds');
    phase('binding');
    need(hash(r)===q.request_binding_sha256&&hash(e)===q.expected_sha256
        &&hash(dispatchSchema)===e.schema_sha256&&hash(schema)===t.schema_sha256
        &&t.dispatch_expected_sha256===q.expected_sha256&&t.dispatch_policy_sha256===e.policy_sha256
        &&t.probe_context_sha256===e.probe_context_sha256&&r.probe_context_sha256===e.probe_context_sha256
        &&r.probe_expected_sha256===e.probe_expected_sha256
        &&hash({transport_expected:t,request_binding_sha256:q.request_binding_sha256})===q.transport_binding_sha256,
        'transport_child_binding');
    const names=Object.keys(q.role_expected_fields);
    need(names.length===5&&Object.keys(r.roles).length===5&&names.every(k=>Object.hasOwn(r.roles,k))
        &&hash(q.role_expected_fields)===e.role_policy_sha256,'transport_child_roles');
    out.request_binding_sha256=q.request_binding_sha256;out.expected_sha256=q.expected_sha256;
    out.transport_binding_sha256=q.transport_binding_sha256;out.probe_context_sha256=e.probe_context_sha256;
    out.identities={};
    for(const role of names){
        check();out.identities[role]={expectation:{...r.roles[role].expectation},observed_sha256:null,expected_match:null};
    }
    phase('result');out.status='SYNTHETIC_FIVE_ROLE_METADATA_NOT_OBSERVED';
}catch(error){
    out.status='SYNTHETIC_FIVE_ROLE_METADATA_REFUSED';out.identities=null;
    const codes=schema.$defs.refusal_code.enum;
    out.error={code:codes.includes(error?.code)?error.code:'transport_child_error',stage};
}finally{request=raw=buffer=null;}
try{
    phase('publication');out.wrapper_steps=steps;out.payload_open_attempts=payloadAttempts;
    validate(out,schema.$defs.node_result,schema,'transport_output');
    const line=Buffer.from(JSON.stringify(out)+'\n','utf8');
    need(line.length<=outputCap,'transport_child_output_budget');
    let offset=0;
    // Positive partial writes advance by at least one byte; no unbounded retry.
    while(offset<line.length){
        need(performance.now()<deadline,'transport_child_deadline');
        const n=write(1,line,offset,line.length-offset);
        need(Number.isSafeInteger(n)&&n>0&&n<=line.length-offset,'transport_stdout');offset+=n;
    }
}catch{process.exitCode=1;}

'use strict';
// Test-only original synthetic text. Guards begin after first-party module loading.
const fs=require('node:fs'),vm=require('node:vm'),Module=require('node:module'),crypto=require('node:crypto');
const request=JSON.parse(fs.readFileSync(0,'utf8')),stdout=process.stdout;
const source=require('../../core/compiler_span_selection.cjs');
const {_canonicalCompilerStreamMetadata:canonical}=require('../../core/compiler_stream_stager.cjs');
const freeze=Object.freeze,from=Buffer.from,original=new RangeError('original synthetic span failure');
let io=0,backend=0,late=0,bad=0,ticks=0,clock=0,out={},text=request.text;
for(const key of ['readFileSync','readSync','openSync','statSync','lstatSync','readdirSync','writeFileSync','readFile','open','writeFile'])fs[key]=()=>{io++;throw Error('payload_io_forbidden');};
for(const key of ['compileFunction','createContext','runInContext','runInNewContext','runInThisContext','Script'])vm[key]=()=>{backend++;throw Error('input_backend_forbidden');};
Module._load=()=>{late++;throw Error('late_module_forbidden');};
const op=request.profiles.private_compiler_span_selection.limits.operation_ms;
if(request.input_kind==='boxed')text=new String(text);
if(request.input_kind==='proxy')text=new Proxy({},{get(){bad++;throw original;}});
if(request.input_kind==='number')text=1;
if(request.input_kind==='null')text=null;
if(request.fault==='expected_getter')Object.defineProperty(request.expected,'invocation_id',{enumerable:true,get(){bad++;throw original;}});
if(request.fault==='profiles_proxy')request.profiles=new Proxy(request.profiles,{get(){bad++;throw original;}});
if(request.fault==='schema_cycle')request.schema.cycle=request.schema;
Object.freeze=value=>{
    if(value?.status==='EXACT_SPAN_CONTENT_SELECTION_RECORDED_NOT_FILE_ATTESTATION'){
        if(request.fault==='late_error')throw original;
        if(request.fault==='late_deadline')clock=op;
    }
    return freeze(value);
};
Buffer.from=(value,...rest)=>{
    const result=from(value,...rest);
    if(typeof value==='string'&&value.startsWith('{')&&value.includes('"status":"EXACT_SPAN_CONTENT_SELECTION_RECORDED_NOT_FILE_ATTESTATION"')){
        if(request.fault==='encode_deadline')clock=op;
        if(request.fault==='encode_error')throw original;
    }
    return result;
};
const now=()=>{
    ticks++;
    if(request.fault==='nan_clock')return NaN;
    if(ticks===2&&request.fault==='backwards_clock')return -1;
    if(ticks===2&&request.fault==='clock_error')throw original;
    if(ticks===2&&request.fault==='caller_mutation'){
        request.expected.origin_ref_sha256='f'.repeat(64);
        request.profiles.private_compiler_span_selection.method='changed_after_copy';
    }
    if(request.fault==='mapping_deadline'&&ticks===8)clock=op;
    return clock;
};
try{
    const deadline=request.fault==='expired'?0:request.fault==='extended'?op+1:op;
    const raw=source.produceSuppliedExactSpanSelection(text,request.expected,request.profiles,request.schema,{now,deadline});
    out.receipt=JSON.parse(raw.toString('utf8'));out.receipt_sha256=crypto.createHash('sha256').update(raw).digest('hex');
    out.receipt_utf8_bytes=raw.length;out.canonical_match=canonical(out.receipt)===raw.toString('utf8');
}catch(error){out.error={code:error?.code||null,type:error?.name||null,stage:source.failureStage(error),original:error===original};out.failure_observation=source.failureObservation(error);}
stdout.write(JSON.stringify({...out,payload_io:io,backend_calls:backend,late_module_calls:late,bad_inputs:bad})+'\n');

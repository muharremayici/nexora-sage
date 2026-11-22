'use strict';
// Constructed metadata only, no source text or producer execution.
const fs=require('node:fs'),vm=require('node:vm'),Module=require('node:module'),crypto=require('node:crypto');
const request=JSON.parse(fs.readFileSync(0,'utf8')),stdout=process.stdout;
const source=require('../../core/compiler_row_span_association.cjs');
const {_canonicalCompilerStreamMetadata:canonical}=require('../../core/compiler_stream_stager.cjs');
const from=Buffer.from,freeze=Object.freeze,parse=JSON.parse,original=new RangeError('original synthetic association failure');
let io=0,backend=0,late=0,bad=0,ticks=0,clock=0,out={},locator=request.locator_text,span=request.span_text;
for(const key of ['readFileSync','readSync','openSync','statSync','lstatSync','readdirSync','writeFileSync','readFile','open','writeFile'])fs[key]=()=>{io++;throw Error('payload_io_forbidden');};
for(const key of ['compileFunction','createContext','runInContext','runInNewContext','runInThisContext','Script'])vm[key]=()=>{backend++;throw Error('input_backend_forbidden');};
Module._load=()=>{late++;throw Error('late_module_forbidden');};
const op=request.profiles.private_compiler_row_span_association.limits.operation_ms;
if(request.input_kind==='boxed')locator=new String(locator);
if(request.input_kind==='buffer')locator=from(locator);
if(request.input_kind==='proxy')locator=new Proxy({},{get(){bad++;throw original;}});
if(request.input_kind==='number')span=1;
if(request.input_kind==='null')span=null;
if(request.fault==='expected_getter')Object.defineProperty(request.request.expected,'invocation_id',{enumerable:true,get(){bad++;throw original;}});
if(request.fault==='profiles_proxy')request.profiles=new Proxy(request.profiles,{get(){bad++;throw original;}});
if(request.fault==='schema_cycle')request.schemas.association.cycle=request.schemas;
Object.freeze=value=>{
    if(value?.status==='SELECTED_ROW_SPAN_METADATA_CONSISTENCY_RECORDED_NOT_ORIGIN_ATTESTATION'){
        if(request.fault==='late_error')throw original;
        if(request.fault==='late_deadline')clock=op;
    }
    return freeze(value);
};
Buffer.from=(value,...rest)=>{
    const raw=from(value,...rest);
    if(typeof value==='string'&&value.startsWith('{')&&value.includes('"status":"SELECTED_ROW_SPAN_METADATA_CONSISTENCY_RECORDED_NOT_ORIGIN_ATTESTATION"')){
        if(request.fault==='encode_error')throw original;
        if(request.fault==='encode_deadline')clock=op;
    }
    return raw;
};
JSON.parse=text=>{if(request.fault==='parse_error'&&text===request.locator_text)throw original;return parse(text);};
const now=()=>{
    ticks++;
    if(request.fault==='nan_clock')return NaN;
    if(request.fault==='backwards_clock'&&ticks===2)return -1;
    if(request.fault==='clock_error'&&ticks===2)throw original;
    if(request.fault==='coverage_deadline'&&ticks===6)clock=op;
    if(request.fault==='caller_mutation'&&ticks===2){request.request.expected.origin_ref_sha256='f'.repeat(64);request.request.decision.span_invocation_id='mutated';}
    return clock;
};
try{
    const deadline=request.fault==='expired'?0:request.fault==='extended'?op+1:op;
    const raw=source.associateSuppliedRowSpanMetadata(locator,span,request.request,request.profiles,request.schemas,{now,deadline});
    out.result=parse(raw.toString('utf8'));out.result_sha256=crypto.createHash('sha256').update(raw).digest('hex');out.result_bytes=raw.length;out.canonical_match=canonical(out.result)===raw.toString('utf8');
}catch(error){out.error={code:error?.code||null,type:error?.name||null,stage:source.failureStage(error),original:error===original};out.failure_observation=source.failureObservation(error);}
stdout.write(JSON.stringify({...out,payload_io:io,backend_calls:backend,late_module_calls:late,bad_inputs:bad})+'\n');

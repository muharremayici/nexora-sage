'use strict';
// Original source lives only in this finite Node fixture. Stdin carries IDs/metadata, never source.
const fs=require('node:fs'),vm=require('node:vm'),Module=require('node:module'),crypto=require('node:crypto');
const DRIVER_CAP=2*1024*1024,chunks=[];let inputBytes=0;
for(;;){const chunk=Buffer.alloc(65536),n=fs.readSync(0,chunk,0,chunk.length,null);if(!n)break;inputBytes+=n;if(inputBytes>DRIVER_CAP)throw Error('driver_metadata_envelope_budget');chunks.push(chunk.subarray(0,n));}
const request=JSON.parse(Buffer.concat(chunks).toString('utf8'));chunks.length=0;
if(Object.keys(request).some(k=>['text','source_text','source_path','raw_source'].includes(k)))throw Error('raw_source_driver_forbidden');
const ONE='const box = { Alpha: () => alpha };\r\n';
const FIXTURES={one:ONE,partial:ONE+' /unsupported',repeated:'const box = { Alpha: () => alpha, Alpha: () => alpha };\r\n',
    no_candidate:'const value = other;                    ',lone:ONE+'\ud800'};
if(!Object.hasOwn(FIXTURES,request.case_id))throw Error('unknown_fixture_id');
const locator=require('../../core/compiler_row_locator.cjs'),span=require('../../core/compiler_span_selection.cjs'),association=require('../../core/compiler_row_span_association.cjs');
const {_canonicalCompilerStreamMetadata:canonical}=require('../../core/compiler_stream_stager.cjs');
let clock=0,io=0,backend=0,late=0,bad=0,ticks=0,stdout=process.stdout,root,firstClock=null;
const dispatched={locator:0,span:0,association:0},entered={locator:0,span:0,association:0},returned={locator:0,span:0,association:0},deadlines=[];
let sameClock=true;const original=new RangeError('original finite composition fixture failure');
const op=request.profiles.private_compiler_row_span_composition.limits.operation_ms;
for(const [stage,api,name]of [['locator',locator,'locateSuppliedCompilerRowCandidates'],['span',span,'produceSuppliedExactSpanSelection'],['association',association,'associateSuppliedRowSpanMetadata']]){
    const native=api[name];api[name]=(...args)=>{
        dispatched[stage]++;const options=args[args.length-1];deadlines.push(options.deadline);
        if(firstClock===null)firstClock=options.now;else sameClock=sameClock&&firstClock===options.now;
        if(request.fault===stage+'_error')throw original;
        if(request.fault==='native_'+stage+'_refusal'){
            if(stage==='locator')args[1]={...args[1],source_text_sha256:'0'.repeat(64)};
            if(stage==='span')args[1]={...args[1],source_text_utf8_sha256:'0'.repeat(64)};
            if(stage==='association')args[2]={...args[2],expected:{...args[2].expected,locator_result_sha256:'0'.repeat(64)}};
        }
        entered[stage]++;let value=native(...args);returned[stage]++;
        if(request.fault==='after_'+stage+'_deadline')clock=op;
        if(request.fault==='tamper_span'&&stage==='span')value[0]=0xff;
        if(request.fault?.startsWith('tamper_association')&&stage==='association'){
            const parsed=JSON.parse(value.toString('utf8'));
            if(request.fault==='tamper_association_source')parsed.source.source_text_utf8_sha256='0'.repeat(64);
            else if(request.fault==='tamper_association_coverage')parsed.coverage.observation_sha256='0'.repeat(64);
            else if(request.fault==='tamper_association_cost')parsed.cost.input_bytes_hashed=0;
            else parsed.flags.origin_authenticated=true;
            value=Buffer.from(canonical(parsed));
        }
        return value;
    };
}
root=require('../../core/compiler_row_span_composition.cjs');
const freeze=Object.freeze,from=Buffer.from,parse=JSON.parse;
for(const key of ['readFileSync','readSync','openSync','statSync','lstatSync','readdirSync','writeFileSync','readFile','open','writeFile'])fs[key]=()=>{io++;throw Error('payload_io_forbidden');};
for(const key of ['compileFunction','createContext','runInContext','runInNewContext','runInThisContext','Script'])vm[key]=()=>{backend++;throw Error('input_backend_forbidden');};
Module._load=()=>{late++;throw Error('late_module_forbidden');};
let text=FIXTURES[request.case_id];
if(request.input_kind==='boxed')text=new String(text);
if(request.input_kind==='proxy')text=new Proxy({},{get(){bad++;throw original;}});
if(request.input_kind==='null')text=null;
if(request.fault==='expected_getter')Object.defineProperty(request.expected.invocations,'composition',{enumerable:true,get(){bad++;throw original;}});
if(request.fault==='profiles_proxy')request.profiles=new Proxy(request.profiles,{get(){bad++;throw original;}});
if(request.fault==='schema_cycle')request.schemas.composition.cycle=request.schemas;
Object.freeze=value=>{
    if(value?.status==='SUPPLIED_SYNTHETIC_ROW_SPAN_COMPOSITION_RECORDED_NOT_ORIGIN_ATTESTATION'){
        if(request.fault==='late_error')throw original;if(request.fault==='late_deadline')clock=op;
    }
    if(value?.association_receipt&&value?.composition_receipt){
        if(request.fault==='bundle_error')throw original;if(request.fault==='bundle_deadline')clock=op;
    }
    return freeze(value);
};
Buffer.from=(value,...rest)=>{
    const raw=from(value,...rest);
    if(typeof value==='string'&&value.startsWith('{')&&value.includes('"status":"SUPPLIED_SYNTHETIC_ROW_SPAN_COMPOSITION_RECORDED_NOT_ORIGIN_ATTESTATION"')){
        if(request.fault==='encode_error')throw original;if(request.fault==='encode_deadline')clock=op;
    }
    return raw;
};
JSON.parse=value=>{if(request.fault==='association_parse_error'&&value.includes('"status":"SELECTED_ROW_SPAN_METADATA_CONSISTENCY_RECORDED_NOT_ORIGIN_ATTESTATION"'))throw original;return parse(value);};
const now=()=>{
    ticks++;
    if(request.fault==='nan_clock')return NaN;if(request.fault==='backwards_clock'&&ticks===2)return -1;
    if(request.fault==='clock_error'&&ticks===2)throw original;
    if(request.fault==='caller_mutation'&&ticks===2){request.expected.origin_ref_sha256='f'.repeat(64);request.expected.candidate.view_text_sha256='f'.repeat(64);}
    return clock;
};
let out={};
try{
    const deadline=request.fault==='expired'?0:request.fault==='extended'?op+1:op;
    const result=root.composeSuppliedSyntheticRowSpan(text,request.expected,request.profiles,request.schemas,{now,deadline});
    out.composition=parse(result.composition_receipt.toString('utf8'));out.association=parse(result.association_receipt.toString('utf8'));
    out.composition_sha256=crypto.createHash('sha256').update(result.composition_receipt).digest('hex');
    out.association_sha256=crypto.createHash('sha256').update(result.association_receipt).digest('hex');
    out.composition_bytes=result.composition_receipt.length;out.association_bytes=result.association_receipt.length;
    out.canonical_match=canonical(out.composition)===result.composition_receipt.toString('utf8')&&canonical(out.association)===result.association_receipt.toString('utf8');
}catch(error){out.error={code:error?.code||null,type:error?.name||null,stage:root.failureStage(error),original:error===original};out.failure_observation=root.failureObservation(error);}
stdout.write(JSON.stringify({...out,dispatched,entered,returned,deadlines,same_clock:sameClock,payload_io:io,backend_calls:backend,late_module_calls:late,bad_inputs:bad,
    driver_input_bytes:inputBytes,driver_envelope_cap:DRIVER_CAP,source_received_via_driver:false})+'\n');

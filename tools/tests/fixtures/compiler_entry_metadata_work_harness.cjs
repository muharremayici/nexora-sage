'use strict';
// Isolated authored-metadata controls; never imports an entry owner/producer.
const fs=require('node:fs'),crypto=require('node:crypto'),Module=require('node:module');
const request=JSON.parse(fs.readFileSync(0,'utf8'));
const shared=require('../../core/compiler_stream_stager.cjs');
const originalCanonical=shared._canonicalCompilerStreamMetadata,originalHash=crypto.createHash;
const original=new RangeError('controlled direct metadata operation failure');
let canonicalCalls=0,hashCalls=0,lastCanonical=null,sameText=true,forbidden=0;
shared._canonicalCompilerStreamMetadata=value=>{
    canonicalCalls++;
    if(request.canonical_fail_at===canonicalCalls)throw original;
    lastCanonical=originalCanonical(value);return lastCanonical;
};
crypto.createHash=function(...args){
    hashCalls++;if(request.hash_fail_at===hashCalls)throw original;
    const h=originalHash(...args),update=h.update;
    h.update=function(value,...rest){sameText=sameText&&typeof value==='string'&&value===lastCanonical;return update.call(this,value,...rest);};
    return h;
};
const {_createEntryContextMetadataWorkMeter:create}=require('../../core/compiler_entry_metadata_work.cjs');
for(const key of ['readFileSync','readFile','openSync','open','statSync','writeFileSync','appendFileSync'])fs[key]=()=>{forbidden++;throw original;};
Module._load=()=>{forbidden++;throw original;};
let meter=null,setupError=null;const operations=[];
try{meter=create(request.policy,request.output_bytes);}catch(error){setupError={code:error.code??null,original_identity:error===original};}
if(meter)for(const step of request.steps||[]){
    try{
        let value=step.value;if(step.invalid_surrogate)value='\ud800';
        const result=step.op==='seal'?meter.seal():step.op==='mark_producer'?meter.markProducerEntry():meter[step.op](value);
        operations.push({ok:true,result:result??null,snapshot:meter.snapshot()});
    }catch(error){operations.push({ok:false,error:{code:error.code??null,original_identity:error===original},snapshot:meter.snapshot()});}
}
process.stdout.write(JSON.stringify({setup_error:setupError,operations,snapshot:meter?.snapshot()??null,
    canonical_calls:canonicalCalls,hash_calls:hashCalls,same_text:sameText,forbidden_io_calls:forbidden})+'\n');

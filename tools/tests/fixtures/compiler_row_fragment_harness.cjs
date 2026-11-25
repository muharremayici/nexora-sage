'use strict';
// Original supplied synthetic text only; no payload filesystem or input backend.
const fs=require('node:fs'),vm=require('node:vm'),Module=require('node:module');
const request=JSON.parse(fs.readFileSync(0,'utf8')),stdout=process.stdout;
const source=require('../../core/compiler_row_fragment.cjs');
let io=0,backend=0,late=0,bad=0,ticks=0,clock=0;
const original=new RangeError('synthetic row injected failure'),freeze=Object.freeze;
for(const key of ['readFileSync','readSync','openSync','statSync','lstatSync','readdirSync','writeFileSync','readFile','open','writeFile'])fs[key]=()=>{io++;throw Error('payload_io_forbidden');};
for(const key of ['compileFunction','createContext','runInContext','runInNewContext','runInThisContext','Script'])vm[key]=()=>{backend++;throw Error('input_backend_forbidden');};
Module._load=()=>{late++;throw Error('late_module_forbidden');};
const op=request.profiles.private_compiler_row_fragment.resource_limits.operation_ms;
let text=request.text,out={};
if(request.input_kind==='boxed')text=new String(text);
if(request.input_kind==='proxy')text=new Proxy({},{get(){bad++;throw original;}});
if(request.input_kind==='number')text=7;
if(request.input_kind==='null')text=null;
if(request.fault==='expected_getter')Object.defineProperty(request.expected,'invocation_id',{enumerable:true,get(){bad++;throw original;}});
if(request.fault==='profiles_proxy')request.profiles=new Proxy(request.profiles,{get(){bad++;throw original;}});
if(request.fault==='schema_cycle')request.schema.loop=request.schema;
Object.freeze=value=>{
    if(value?.status?.includes('LOCAL_ROW_FRAGMENT')){
        if(request.fault==='late_error')throw original;
        if(request.fault==='late_deadline')clock=op;
    }
    return freeze(value);
};
const now=()=>{
    ticks++;
    if(request.fault==='nan_clock')return NaN;
    if(ticks===2&&request.fault==='backwards_clock')return -1;
    if(ticks===2&&request.fault==='clock_error')throw original;
    if(ticks===2&&request.fault==='caller_mutation')request.profiles.private_compiler_row_fragment.grammar='changed_after_copy';
    return clock;
};
try{
    const deadline=request.fault==='expired'?0:request.fault==='extended'?op+1:op;
    out.result=source.observeSuppliedCompilerRowFragment(text,request.expected,request.profiles,request.schema,{now,deadline});
}catch(error){out.error={code:error?.code||null,type:error?.name||null,stage:source.failureStage(error),original:error===original};}
stdout.write(JSON.stringify({...out,payload_io:io,backend_calls:backend,late_module_calls:late,bad_inputs:bad})+'\n');

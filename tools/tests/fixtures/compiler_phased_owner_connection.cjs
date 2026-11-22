'use strict';
// Import order/outcome probe only. No target or owner/producer execution.
const fs=require('node:fs'),Module=require('node:module');
const req=JSON.parse(fs.readFileSync(0,'utf8'));
const imports={capture:'../../core/compiler_preparation_capture.cjs',window:'../../core/compiler_text_window.cjs',stager:'../../core/compiler_stream_stager.cjs'};
for(const name of req.order)require(imports[name]);
const capture=require(imports.capture),window=require(imports.window),entry=require('../../core/compiler_entry_context_handoff.cjs');
const copy=window.copy,load=Module._load,freeze=Object.freeze,OriginalSet=global.Set;
const out={copies:0,forbidden:0,freezes_during_getter:0,traps:0,states:[],owner_called:false};
const original=req.kind==='primitive'?0:req.kind==='proxy'?new Proxy({}, {get(){out.traps++;throw Error('inspected');},getPrototypeOf(){out.traps++;throw Error('inspected');}}):new RangeError('probe');
window.copy=function(...args){out.copies++;const value=copy(...args);if(req.fault==='copy')throw original;return value;};
Module._load=()=>{out.forbidden++;throw Error('late module');};
const ownError=(site,fn)=>{try{fn();return{site,ok:true};}catch(error){return{site,ok:false,original_identity:error===original};}};
const api=capture._createPreparationCopyAttempt();
const outcome=()=>{const saved=Object.freeze;Object.freeze=()=>{out.freezes_during_getter++;throw Error('allocating outcome');};
    try{return api.copy_outcome;}finally{Object.freeze=saved;}};
try{
    out.states.push(outcome());
    if(req.fault==='setup')global.Set=function(){global.Set=OriginalSet;throw original;};
    const input=req.input;if(req.cycle)input.intent.cycle=input;
    out.copy=ownError('copy',()=>api.copy_input(input));out.states.push(outcome());
    if(out.copy.ok){
        if(req.fault==='snapshot'){Object.freeze=()=>{throw original;};out.snapshot=ownError('snapshot',()=>api.snapshot());Object.freeze=freeze;}
        else{api.copy_observation();api.freeze_owned();const view=api.take_for_context_owner(api.witness);out.frozen=Object.isFrozen(view.held);}
    }else if(req.fault!=='setup')api.copy_observation();
    out.states.push(outcome());api.release();out.states.push(outcome());
    const descriptor=Object.getOwnPropertyDescriptor(api,'copy_outcome');
    out.readonly=Object.isFrozen(api)&&typeof descriptor.get==='function'&&descriptor.set===undefined&&!descriptor.configurable;
    out.owner_arities=[entry.observeSyntheticEntryOwnerContext.length,entry._observeSyntheticEntryOwnerContextWithPhasedCopy.length];
    out.owner_exports=Object.keys(entry).sort();out.final=api.snapshot();
}finally{Object.freeze=freeze;window.copy=copy;Module._load=load;global.Set=OriginalSet;api.release();}
process.stdout.write(JSON.stringify(out));

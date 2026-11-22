"use strict";
const fs=require('node:fs'),Module=require('node:module');
const request=JSON.parse(fs.readFileSync(0,'utf8')),write=fs.writeSync.bind(fs),originalLoad=Module._load;
let forbidden=0,backend=0,traps=0;
Module._load=function(id,...args){if(/compiler_entry_owner|compiler_row_locator|compiler_span_selection|compiler_row_span_association|compiler_selection_controller/.test(id)){forbidden++;throw new Error('producer_or_owner_forbidden');}return originalLoad.call(this,id,...args);};
const api=require('../../core/compiler_owned_view_plan.cjs');
const e=request.expected,p=request.profiles,s=request.schemas,mode=request.mode||'normal',sentinel=new Error('original_clock_failure');
if(mode==='getter')Object.defineProperty(e,'bindings',{enumerable:true,get(){traps++;return {};}});
if(mode==='proxy')e.bindings=new Proxy(e.bindings,{ownKeys(){traps++;return [];}});
if(mode==='cycle')e.bindings.loop=e;
if(mode==='symbol')e[Symbol('extra')]=1;
if(mode==='thenable')e.then=()=>{traps++;};
if(mode==='nan')e.costs.owner_metadata_cap=NaN;
if(mode==='infinity')e.costs.owner_metadata_cap=Infinity;
if(mode==='undefined')e.costs.owner_metadata_cap=undefined;
for(const k of ['readFileSync','openSync','statSync','fstatSync','realpathSync','readdirSync','readSync','writeFileSync','appendFileSync'])fs[k]=()=>{backend++;throw new Error('backend_forbidden');};
Module._load=()=>{forbidden++;throw new Error('late_module_forbidden');};
let ticks=0,calls=0,result;
const now=()=>{ticks++;if(mode==='clock_throw'&&ticks===10)throw sentinel;if(mode==='clock_backwards'&&ticks===10)return -1;return request.expire_at&&ticks>=request.expire_at?30000:0;};
try{calls++;const out=api.planSuppliedOwnedViewMetadata(e,p,s,{now,deadline:request.deadline??30000});result={ok:true,receipt:JSON.parse(out.toString('utf8')),bytes:out.length};}
catch(error){result={ok:false,code:error.code||error.name,observation:api.failureObservation(error),original_clock_failure_preserved:error===sentinel};}
write(1,JSON.stringify({...result,planner_calls:calls,forbidden_modules:forbidden,backend_attempts:backend,trap_invocations:traps,ticks}));

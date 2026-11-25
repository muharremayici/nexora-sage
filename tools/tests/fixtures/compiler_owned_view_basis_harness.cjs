"use strict";
const fs=require('node:fs'),Module=require('node:module');
const request=JSON.parse(fs.readFileSync(0,'utf8')),write=fs.writeSync.bind(fs),load=Module._load;
let blocked=0,backends=0,traps=0;
Module._load=function(id,...args){if(/compiler_entry_owner|compiler_owned_view_plan\.cjs|compiler_selection_controller|compiler_row_locator|compiler_span_selection|compiler_row_span_association/.test(id)){blocked++;throw new Error('producer_planner_owner_forbidden');}return load.call(this,id,...args);};
const api=require('../../core/compiler_owned_view_basis.cjs'),e=request.expected,p=request.profiles,s=request.schemas,mode=request.mode||'normal',sentinel=new Error('original_clock_error');
function reverseObjectKeys(value){if(Array.isArray(value))return value.map(reverseObjectKeys);if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).reverse().map(([k,v])=>[k,reverseObjectKeys(v)]));return value;}
if(mode==='reverse_object_keys'){for(const root of [e,p,s]){const changed=reverseObjectKeys(root);for(const k of Object.keys(root))delete root[k];Object.assign(root,changed);}}
if(mode==='getter')Object.defineProperty(e,'basis',{enumerable:true,get(){traps++;return {};}});
if(mode==='proxy')e.basis=new Proxy(e.basis,{ownKeys(){traps++;return [];}});
if(mode==='cycle')e.basis.loop=e;
if(mode==='symbol')e[Symbol('extra')]=1;
if(mode==='thenable')e.then=()=>{traps++;};
if(mode==='nan')e.basis.caps.owner_metadata_cap_bytes=NaN;
if(mode==='infinity')e.basis.caps.owner_metadata_cap_bytes=Infinity;
if(mode==='undefined')e.basis.caps.owner_metadata_cap_bytes=undefined;
for(const key of ['readFileSync','openSync','statSync','fstatSync','realpathSync','readdirSync','readSync','writeFileSync','appendFileSync'])fs[key]=()=>{backends++;throw new Error('backend_forbidden');};
Module._load=()=>{blocked++;throw new Error('late_module_forbidden');};
let ticks=0,result,calls=0;
const now=()=>{ticks++;if(mode==='clock_throw'&&ticks===10)throw sentinel;if(mode==='clock_backwards'&&ticks===10)return -1;return request.expire_at&&ticks>=request.expire_at?30000:0;};
try{calls++;const out=api.checkSuppliedOwnedViewCostBasis(e,p,s,{now,deadline:request.deadline??30000});result={ok:true,receipt:JSON.parse(out.toString('utf8')),bytes:out.length};}
catch(error){result={ok:false,code:error.code||error.name,observation:api.failureObservation(error),original_error_preserved:error===sentinel};}
write(1,JSON.stringify({...result,ticks,checker_calls:calls,blocked_modules:blocked,backend_attempts:backends,trap_invocations:traps}));

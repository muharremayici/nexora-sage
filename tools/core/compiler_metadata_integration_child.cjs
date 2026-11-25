'use strict';
function runChild(historical=false){
// One trusted-parent synthetic child, never a compiler evaluator or generic backend.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto'),Module=require('node:module'),vm=require('node:vm');
const {performance}=require('node:perf_hooks');
const started=performance.now();
const owner=require('./compiler_entry_owner.cjs'),joint=require('./compiler_canonical_envelope.cjs');
const { _canonicalCompilerStreamMetadata:canonical }=require('./compiler_stream_stager.cjs');
const native={};for(const k of ['readSync','openSync','closeSync','fstatSync','statSync','lstatSync','realpathSync','readFileSync','readFile','open','readdirSync','readdir','writeFileSync','writeFile','appendFileSync','writeSync'])native[k]=fs[k];
const originalLoad=Module._load,originalVm={};
const norm=v=>process.platform==='win32'?path.resolve(v).toLowerCase():path.resolve(v);
const sha=v=>crypto.createHash('sha256').update(v).digest('hex'),hash=v=>sha(canonical(v));
const fail=code=>{const e=new Error(code);e.code=code;throw e;};
const need=(v,code)=>{if(!v)fail(code);};
const inside=(base,target)=>{const r=path.relative(base,target);return r!==''&&r!=='..'&&!r.startsWith('..'+path.sep)&&!path.isAbsolute(r);};
const cap=Number(process.argv[2]),outputCap=Number(process.argv[3]),remaining=Number(process.argv[4]);
let request=null,raw=null,deadline=null,stage='stdin',armed=false,steps=0;
let late=0,unscoped=0,backends=0,openAttempts=0,openReturns=0,openUnknown=0,reads=0,readUnknown=0,closeAttempts=0,closeReturns=0,closeUnknown=0,delivered=0,emergency=0;
const fds=new Map(),roleReads=[];
const out={version:historical?'v1-fixed-historical-metadata-observer':'v1-fixed-temporary-metadata-observer',status:historical?'HISTORICAL_FOUR_METADATA_OBSERVER_REFUSED':'SYNTHETIC_FOUR_METADATA_OBSERVER_REFUSED',
 context_sha256:null,decision_binding_sha256:null,dispatch_request_sha256:null,observation:null,
 error:null,payload_accounting:null,late_module_loads:0,unscoped_accesses:0,backend_attempts:0,wrapper_steps:0};
function check(){
    need(deadline!==null&&performance.now()<deadline,'integration_child_deadline');
    if(armed){steps++;need(steps<=request.wrapper_steps,'integration_wrapper_steps');}
}
try{
    need([cap,outputCap,remaining].every(n=>Number.isSafeInteger(n)&&n>0),'integration_child_bounds');
    deadline=started+remaining;
    let buffer=Buffer.alloc(cap+1),count=0;
    while(count<buffer.length){check();const n=native.readSync(0,buffer,count,buffer.length-count,null);need(Number.isSafeInteger(n)&&n>=0&&n<=buffer.length-count,'integration_stdin');if(n===0)break;count+=n;}
    need(count<=cap,'integration_stdin_budget');raw=buffer.subarray(0,count);buffer=null;
    request=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw));
    need(Buffer.from(canonical(request),'utf8').equals(raw),'integration_noncanonical_stdin');raw=null;
    need(request.version===out.version,'integration_child_version');
    out.context_sha256=request.context_sha256;out.decision_binding_sha256=request.decision_binding_sha256;out.dispatch_request_sha256=request.dispatch_request_sha256;
    const o=request.observer,scope=o.expected.scope,p=o.profiles[historical?'private_compiler_historical_metadata_observer':'private_compiler_metadata_only_observer'],basis=o.profiles.private_compiler_owned_view_basis;
    need(Number.isSafeInteger(request.operation_ms)&&request.operation_ms>0&&remaining<=request.operation_ms&&request.operation_ms<=p.limits.operation_ms,'integration_child_deadline');
    need(hash(o)===request.context_sha256,'integration_child_context');
    need(Number.isSafeInteger(request.wrapper_steps)&&request.wrapper_steps>0&&request.wrapper_steps<=p.limits.primitive_steps,'integration_wrapper_steps');
    const root=path.resolve(o.root),temp=path.resolve(os.tmpdir());
    need(typeof o.root==='string'&&path.isAbsolute(o.root)&&(historical?root!==path.parse(root).root:inside(temp,root))
        &&!o.root.split(/[\\/]/).includes('..'),'integration_child_temporary_root');
    // Check each component before realpath can follow a synthetic alias.
    const ancestor=historical?path.parse(root).root:temp;
    let cursor=ancestor;
    for(const part of path.relative(ancestor,root).split(path.sep)){
        cursor=path.join(cursor,part);const s=native.lstatSync(cursor);
        need(s.isDirectory()&&!s.isSymbolicLink(),'integration_child_temporary_root');
    }
    need(norm(native.realpathSync(root))===norm(root)&&scope.root_text===norm(root),'integration_child_root');
    const names=basis.metadata_roles;
    need(Array.isArray(names)&&names.length===4&&!names.includes(basis.entry_role)
        &&Object.keys(scope.roles).length===4&&names.every(k=>Object.hasOwn(scope.roles,k)),'integration_child_roles');
    const allowed=new Map();
    for(const role of names){
        const r=scope.roles[role];
        need(typeof r.relative_path==='string'&&!path.isAbsolute(r.relative_path)&&!r.relative_path.split('/').includes('..'),'integration_child_path');
        const target=path.join(root,...r.relative_path.split('/'));
        need(inside(root,target)&&!allowed.has(norm(target))&&Number.isSafeInteger(r.max_raw_bytes)&&r.max_raw_bytes>0&&r.max_raw_bytes<=p.scope_limits.raw_bytes,'integration_child_path');
        allowed.set(norm(target),{role,cap:r.max_raw_bytes});
    }
    const scoped=v=>{
        need(typeof v==='string','integration_child_path');
        const n=norm(v);if(n!==norm(root)&&!allowed.has(n)){unscoped++;fail('integration_unscoped_path');}return n;
    };
    armed=true;
    fs.realpathSync=value=>{check();scoped(value);const r=native.realpathSync(value);scoped(r);return r;};
    fs.statSync=value=>{check();scoped(value);return native.statSync(value);};
    fs.lstatSync=value=>{check();scoped(value);return native.lstatSync(value);};
    fs.openSync=(value,mode,...rest)=>{
        check();const n=scoped(value),r=allowed.get(n);openAttempts++;
        need(r&&mode==='r'&&openAttempts<=scope.max_open_attempts,'integration_open_scope');
        let fd;try{fd=native.openSync(value,mode,...rest);openReturns++;}catch(e){openUnknown++;throw e;}
        const record={role:r.role,cap:r.cap,known_delivered_bytes:0,read_attempts:0,closed:false};
        roleReads.push(record);fds.set(fd,record);return fd;
    };
    fs.fstatSync=fd=>{check();need(fds.has(fd),'integration_descriptor');return native.fstatSync(fd);};
    fs.readSync=(fd,buffer,offset,length,position)=>{
        check();const r=fds.get(fd);need(r,'integration_descriptor');reads++;r.read_attempts++;
        need(Number.isSafeInteger(length)&&length>=0&&length<=r.cap+1-r.known_delivered_bytes
            &&length<=scope.max_delivered_bytes-delivered,'integration_read_budget');
        let n;try{n=native.readSync(fd,buffer,offset,length,position);}catch(e){readUnknown++;throw e;}
        if(!Number.isSafeInteger(n)||n<0||n>length){readUnknown++;fail('integration_read_unknown');}
        r.known_delivered_bytes+=n;delivered+=n;return n;
    };
    fs.closeSync=fd=>{
        const r=fds.get(fd);need(r,'integration_descriptor');closeAttempts++;
        try{native.closeSync(fd);}catch(e){closeUnknown++;throw e;}
        r.closed=true;fds.delete(fd);closeReturns++;
    };
    for(const k of ['readFileSync','readFile','open','readdirSync','readdir','writeFileSync','writeFile','appendFileSync','writeSync'])
        fs[k]=()=>{unscoped++;fail('integration_unowned_io');};
    Module._load=()=>{late++;fail('integration_late_module');};
    for(const k of ['compileFunction','createContext','runInContext','runInNewContext','runInThisContext','Script']){
        originalVm[k]=vm[k];vm[k]=()=>{backends++;fail('integration_backend');};
    }
    armed=true;stage='observer';
    const observe=historical?owner.observeHistoricalCompilerMetadataPair:owner.observeSyntheticCompilerMetadataPair;
    const result=observe(root,o.expected,o.profiles,o.schemas,{now:()=>performance.now(),deadline});
    // The bounded pipe receiver owns full result-schema validation. The fixed
    // child owns only scoped I/O and observer settlement, without a second schema engine.
    check();stage='result_accounting';
    need(result.binding_sha256===o.expected.binding_sha256&&openAttempts===openReturns&&openReturns===closeReturns
        &&openReturns===names.length&&fds.size===0&&!openUnknown&&!readUnknown&&!closeUnknown,'integration_final_accounting');
    out.observation=result;out.status=historical?'HISTORICAL_FOUR_METADATA_OBSERVER_COMPLETED':'SYNTHETIC_FOUR_METADATA_OBSERVER_COMPLETED';
}catch(error){
    out.error={code:typeof error?.code==='string'?error.code:'integration_child_error',stage,
        observer_failure:owner.manifestCapsuleFailureObservation(error)};
    out.observation=null;
}finally{
    for(const [fd,r]of fds){emergency++;try{native.closeSync(fd);r.closed=true;closeReturns++;fds.delete(fd);}catch{closeUnknown++;}}
    if(emergency){out.status=historical?'HISTORICAL_FOUR_METADATA_OBSERVER_REFUSED':'SYNTHETIC_FOUR_METADATA_OBSERVER_REFUSED';out.observation=null;out.error??={code:'integration_emergency_cleanup',stage:'cleanup',observer_failure:null};}
    if(armed){for(const [k,v]of Object.entries(native))fs[k]=v;Module._load=originalLoad;for(const [k,v]of Object.entries(originalVm))vm[k]=v;}
    request=raw=null;
}
out.payload_accounting={open_attempts:openAttempts,open_returns:openReturns,open_unknown:openUnknown,
 read_attempts:reads,read_unknown:readUnknown,close_attempts:closeAttempts,close_returns:closeReturns,close_unknown:closeUnknown,
 known_delivered_bytes:delivered,known_open_descriptors:openUnknown||closeUnknown?null:fds.size,
 emergency_closes:emergency,roles:roleReads,coverage:historical?'four_declared_metadata_payloads_only':'four_temporary_metadata_payloads_only'};
out.late_module_loads=late;out.unscoped_accesses=unscoped;out.backend_attempts=backends;out.wrapper_steps=steps;
if(deadline===null||performance.now()>=deadline){out.status=historical?'HISTORICAL_FOUR_METADATA_OBSERVER_REFUSED':'SYNTHETIC_FOUR_METADATA_OBSERVER_REFUSED';out.observation=null;out.error={code:'integration_child_deadline',stage:'final',observer_failure:null};}
let serialized=JSON.stringify(out)+'\n';
need(Number.isSafeInteger(outputCap)&&outputCap>0&&Buffer.byteLength(serialized,'utf8')<=outputCap,'integration_child_output_budget');
process.stdout.write(serialized);


}
if(require.main===module)runChild();
module.exports={_runHistoricalMetadataChild:()=>runChild(true)};

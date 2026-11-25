'use strict';
// Test-only first-party adapter. Every payload open is confined to a temporary fixture.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),Module=require('node:module');
const request=JSON.parse(fs.readFileSync(0,'utf8')),mode=request.mode||'normal';
const joint=require('../../core/compiler_canonical_envelope.cjs'),realJoint=joint.encodeSyntheticCanonicalMetadataWithStructure;
const stringify=JSON.stringify,parse=JSON.parse,freeze=Object.freeze,sentinel=new RangeError('controlled_manifest_failure');
let childCalls=0,childReturns=0,manifestParses=0,capsuleSeen=0,ownerSeen=null,summarySeen=false,traps=0,late=0,reentered=false;
joint.encodeSyntheticCanonicalMetadataWithStructure=function(...args){
    childCalls++;if(mode==='child_throw')throw sentinel;
    const out=realJoint(...args);childReturns++;
    if(mode==='payload_digest')out.payload[0]^=1;
    if(mode==='structure_digest')out.structure[0]^=1;
    if(mode==='receipt_digest')out.receipt[0]^=1;
    if(mode==='receipt_hash')out.receiptSha256='0'.repeat(64);
    if(mode==='extra_output')out.extra=1;
    if(mode==='proxy_output')return new Proxy(out,{get(){traps++;throw sentinel;}});
    if(mode==='getter_output')Object.defineProperty(out,'payload',{get(){traps++;throw sentinel;}});
    if(mode==='shared_payload')out.payload=Buffer.from(new SharedArrayBuffer(10));
    if(mode==='detached_payload'){const b=Buffer.alloc(20000);structuredClone(b.buffer,{transfer:[b.buffer]});out.payload=b;}
    if(['fake_receipt','receipt_anchor','receipt_flag','receipt_pair','receipt_calls','receipt_work','malformed_receipt'].includes(mode)){
        const r=parse(out.receipt.toString('utf8'));
        if(mode==='fake_receipt')r.extra=true;
        if(mode==='receipt_anchor')r.intent_sha256='0'.repeat(64);
        if(mode==='receipt_flag')r.flags.runtime=true;
        if(mode==='receipt_pair')r.joint_pair_sha256='0'.repeat(64);
        if(mode==='receipt_calls')r.calls[request.counter||'capture_completed']=0;
        if(mode==='receipt_work')r.metadata_byte_work_units=0;
        out.receipt=Buffer.from(mode==='malformed_receipt'?'{':stringify(r));
        out.receiptSha256=crypto.createHash('sha256').update(out.receipt).digest('hex');
    }
    if(mode==='settlement')out.settlement.metadata_byte_work_units=0;
    if(mode==='settlement_proxy')out.settlement=new Proxy(out.settlement,{ownKeys(){traps++;throw sentinel;}});
    if(mode==='settlement_primitive_budget')out.settlement.extra='x'.repeat(8*request.profiles.private_compiler_manifest_capsule.limits.output_bytes);
    return out;
};
const filename=require.resolve('../../core/compiler_entry_owner.cjs');
let body=fs.readFileSync(filename,'utf8');
const probes={
    fake_capsule:'token={};',
    wrong_parent:'capsules.get(token).parent={};',
    wrong_role:"capsules.get(token).role='lockfile';",
    wrong_read:"capsules.get(token).record={...capsules.get(token).record,raw_sha256:'0'.repeat(64)};"
};
if(mode in probes){
    const marker='consume(token,c);c.check();';if(!body.includes(marker))throw Error('missing private test probe');
    body=body.replace(marker,probes[mode]+marker).replace('const token=Object.freeze({toJSON()','let token=Object.freeze({toJSON()');
}
if(mode==='second_consume')body=body.replace('consume(token,c);c.check();','consume(token,c);consume(token,c);c.check();');
if(mode==='second_mint')body=body.replace('consume(token,c);c.check();','consume(token,c);stage(c,parsed,record,expected);c.check();');
const unit=new Module(filename);unit.filename=filename;unit.paths=module.paths;unit._compile(body,filename);const api=unit.exports;
const root=path.resolve(request.root),sandbox=path.resolve(request.sandbox),native={};
for(const k of ['openSync','readSync','closeSync','fstatSync','statSync','realpathSync','writeSync'])native[k]=fs[k];
let opens=0,closes=0,readBytes=0,lastRole='',forbidden=0,ticks=0;const fds=new Map(),rolePaths=request.role_paths;
const scoped=v=>{const p=path.resolve(String(v)),r=path.relative(sandbox,p);if(r==='..'||r.startsWith('..'+path.sep)||path.isAbsolute(r)){forbidden++;throw Error('outside temporary fixture');}return p;};
fs.realpathSync=v=>{scoped(v);const r=native.realpathSync(v);scoped(r);return r;};
fs.statSync=v=>{scoped(v);return native.statSync(v);};
fs.openSync=(v,...args)=>{const p=scoped(v),fd=native.openSync(v,...args);fds.set(fd,p);opens++;return fd;};
fs.readSync=(fd,...args)=>{const n=native.readSync(fd,...args);readBytes+=n;return n;};
fs.closeSync=fd=>{const p=fds.get(fd);native.closeSync(fd);fds.delete(fd);closes++;lastRole=Object.keys(rolePaths).find(k=>path.resolve(rolePaths[k])===p)||'';
    if(mode==='late_close_throw'&&lastRole==='entry')throw sentinel;};
JSON.parse=(v,reviver)=>{if(lastRole==='manifest'&&typeof reviver==='function')manifestParses++;return parse(v,reviver);};
Object.freeze=v=>{
    if(v&&typeof v==='object'&&typeof v.snapshot==='function'&&typeof v.dispose==='function')ownerSeen=v;
    if(v&&typeof v==='object'&&typeof v.toJSON==='function'&&!v.snapshot){capsuleSeen++;if(mode==='capsule_privacy'){
        try{stringify(v);throw Error('capsule serialized');}catch(e){if(e.message!=='manifest_capsule_not_serializable')throw e;}
    }}
    if(v?.status==='SYNTHETIC_MANIFEST_SAME_READ_PAIR_OBSERVED_NOT_OWNER_ADMISSION')summarySeen=true;
    return freeze(v);
};
const options={deadline:30000,now:()=>{
    ticks++;
    if(mode==='clock_throw'&&ticks===10)throw sentinel;
    if(mode==='clock_backwards'&&ticks===2)return -1;
    if(mode==='clock_nonfinite'&&ticks===2)return NaN;
    if(mode==='initial_deadline')return 30000;
    if(request.expire_at&&ticks>=request.expire_at)return 30000;
    if(mode==='child_deadline'&&childReturns)return 30000;
    if(mode==='disposal_deadline'&&ownerSeen?.snapshot().state==='DISPOSED')return 30000;
    if(mode==='publication_deadline'&&summarySeen)return 30000;
    if(!reentered&&((mode==='reentry'&&ticks===2)||(mode==='capsule_reentry'&&capsuleSeen))){
        reentered=true;try{api.observeSyntheticManifestPairCapture(root,request.expected,request.profiles,request.schemas,options);}catch{}
    }
    if(mode==='mutate_inputs'&&capsuleSeen){request.expected.entry_expected.manifest_sha256='0'.repeat(64);request.profiles.private_compiler_metadata_joint.status='changed';}
    return 0;
}};
if(mode==='proxy_options')Object.setPrototypeOf(options,new Proxy({},{get(){traps++;}}));
if(mode==='getter_options')Object.defineProperty(options,'now',{get(){traps++;throw sentinel;}});
if(mode==='proxy_context')request.profiles=new Proxy(request.profiles,{ownKeys(){traps++;throw sentinel;}});
if(mode==='getter_context')Object.defineProperty(request.expected,'binding_sha256',{get(){traps++;throw sentinel;}});
if(mode==='callback_option')options.callback=()=>{traps++;};
for(const k of ['readFileSync','writeFileSync','appendFileSync','readdirSync','open','readFile'])fs[k]=()=>{forbidden++;throw Error('unowned fixture IO');};
Module._load=()=>{late++;throw Error('late backend forbidden');};
let out;
try{
    const result=api.observeSyntheticManifestPairCapture(root,request.expected,request.profiles,request.schemas,options);
    out={ok:true,result,frozen:Object.isFrozen(result)&&Object.isFrozen(result.pair)&&Object.isFrozen(result.child.calls)};
}catch(e){out={ok:false,code:e.code||e.name,original_error:e===sentinel,observation:api.manifestCapsuleFailureObservation(e)};}
JSON.parse=parse;Object.freeze=freeze;
const snapshot=ownerSeen?.snapshot();
process.stdout.write(stringify({...out,ticks,childCalls,childReturns,manifestParses,capsuleSeen,traps,forbidden,late,opens,closes,open_fds:fds.size,readBytes,
    final_owner:snapshot?{state:snapshot.state,registered:snapshot.entry_owner_registered,bytes:snapshot.retained_entry_bytes}:null,
    public_capsule_hooks:Object.keys(api).filter(k=>/manifest.*(?:consume|stage|mint)/i.test(k)),entry_evaluated:globalThis.__ENTRY_OWNER_MARKER__===true}));

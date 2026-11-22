'use strict';
// One bounded synthetic request. This adapter is not a production stream driver.
const fs = require('node:fs');
const request = JSON.parse(fs.readFileSync(0,'utf8'));
const crypto = require('node:crypto');
const { performance } = require('node:perf_hooks');
const image = require('../../core/ts_input_image_experiment.cjs');
const stagerModule = require('../../core/compiler_stream_stager.cjs');
let forbidden = 0, calls = 0, clock = 0, preparing = false, finishDone = false, afterFinish = 0;
let copyCount = 0, finishCount = 0;
const original = new RangeError('controlled private test failure');
const from = Buffer.from, hash = crypto.createHash;
const copy = image._copyDecodeOwnedText, finish = image._finishOwnedText;
const deny = () => { forbidden++; throw Error('forbidden fixture dependency'); };
for (const key of ['createExperiment','admitCompilerPackageMetadata','admitTypingPackages']) image[key]=deny;
image._copyDecodeOwnedText = raw => {
    if (preparing) copyCount++;
    if (preparing && request.fault==='later_copy_error' && copyCount===(request.fault_index??2)) throw original;
    if (preparing && request.fault==='mutate_later_before_copy' && copyCount===(request.fault_index??2) && raw.length) raw[0]^=1;
    if (request.fault==='mutate_before_copy' && preparing && raw.length) raw[0]^=1;
    const value = copy(raw);
    if (request.fault==='mutate_after_copy' && preparing) raw.fill(120);
    return value;
};
image._finishOwnedText = phase => {
    if (preparing && request.fault==='later_hash_error' && finishCount+1===(request.fault_index??2)) throw original;
    const value=finish(phase);
    if (preparing) {finishCount++;finishDone=true;}
    return value;
};
const { createCompilerDeclarationTextPreparation, createCompilerDeclarationTextSetPreparation,
    createCompilerDeclarationTextHandoffPreparation, _borrowPreparedCompilerTextSet, DeclarationTextRefusal } = require('../../core/compiler_declaration_text.cjs');
const Module = require('node:module'), load = Module._load;
let handoffFactory = createCompilerDeclarationTextHandoffPreparation;
if (request.foreign_constructor) {
    // Separate producer module instance, sharing only canonical dependencies.
    const filename = require.resolve('../../core/compiler_declaration_text.cjs');
    const foreign = new Module(filename); foreign.filename=filename;foreign.paths=module.paths;
    foreign._compile(fs.readFileSync(filename,'utf8'),filename);
    handoffFactory=foreign.exports.createCompilerDeclarationTextHandoffPreparation;
}
Module._load = function (name,...args) {
    if (!['node:crypto','node:path','node:perf_hooks','node:util'].includes(name)) return deny();
    return load.call(this,name,...args);
};
for (const name of ['readFileSync','readFile','openSync','open','statSync','stat','readdirSync','readdir','writeFileSync','writeFile']) fs[name]=deny;
Buffer.from = function (value,...args) {
    if (preparing && request.fault==='copy_error' && Buffer.isBuffer(value)) throw original;
    return from.call(Buffer,value,...args);
};
crypto.createHash = function (...args) {
    if (preparing && request.fault==='hash_error') throw original;
    return hash.apply(this,args);
};
const projectOne = row => ({raw_sha256:row.raw_sha256,parser_sha256:row.parser_sha256,host_sha256:row.host_sha256,
    size_bytes:row.size_bytes,host_text_utf8_sha256:hash('sha256').update(row.hostText).digest('hex')});
const project = row => Array.isArray(row) ? row.map(item=>({name:item.name,...projectOne(item)})) : projectOne(row);
const error = value => ({error:value instanceof DeclarationTextRefusal || value instanceof image.ImageRefusal || value instanceof stagerModule.StreamStagerRefusal
    ? value.code : value===original ? 'injected_original' : 'unexpected',original_identity:value===original});
const out = {node_version:process.version,events:[],forbidden_accesses:0,compiler_image_calls:0,prepared_returned:false,consumer_calls:0};
let gate, owner, phase, escaped;
let badOwnerCalls=0, expectedGetterCalls=0;
try {
    if (request.mode==='pure') {
        const raw=Buffer.from(request.raw,'base64');
        preparing=true;
        if(request.action==='decoder_error') {
            const Decoder=global.TextDecoder;
            global.TextDecoder=class extends Decoder { decode() {throw original;} };
        }
        phase=image._copyDecodeOwnedText(request.action==='wrong_type' ? new Uint8Array(raw) : raw);
        if(request.action==='mutate_input')raw.fill(120);
        if(request.action==='serialize') {
            let refused=false;try{JSON.stringify(phase);}catch{refused=true;}
            out.events.push({serialization_refused:refused});
        }
        if(request.action==='dispose')phase.dispose();
        try {out.events.push({value:project(image._finishOwnedText(phase))});}
        catch(value) {out.events.push(error(value));}
        if(request.action==='double_finish'||request.fault==='hash_error') {
            try {image._finishOwnedText(phase);out.events.push({unexpected_success:true});}
            catch(value) {out.events.push(error(value));}
        }
    } else {
        const deadline=request.real_clock ? performance.now()+1000 : (request.deadline ?? 1000);
        const now=()=>{
            if(request.fault==='between_files' && finishCount) {
                afterFinish++;if(afterFinish>=2)return deadline;
            }
            if(finishDone && finishCount>=(request.multiple ? request.manifest.files.length : 1)
                && ['stager_post_deadline','gate_post_deadline','publication_deadline'].includes(request.fault)) {
                afterFinish++;
                if(afterFinish>=({stager_post_deadline:2,gate_post_deadline:3,publication_deadline:4}[request.fault]))return deadline;
            }
            return clock;
        };
        const create=request.handoff ? handoffFactory : request.multiple ? createCompilerDeclarationTextSetPreparation : createCompilerDeclarationTextPreparation;
        gate=create(request.expected,request.manifest,request.profiles,request.transport_schema,request.text_schema,
            request.real_clock ? {deadline} : {deadline,now});
        const independentBorrowExpected=(request.handoff || request.steps?.some(step=>step.op==='borrow')) ? {context_sha256:request.expected.context_sha256,inventory_sha256:request.expected.inventory_sha256,
            manifest_sha256:request.expected.manifest_sha256,
            preparation_policy_sha256:image.hashObject(request.profiles.private_compiler_declaration_text_handoff_preparation),
            preparation_schema_sha256:image.hashObject(request.text_schema),
            image_policy_sha256:image.hashObject(request.profiles[request.profiles.private_compiler_stream_layout.image_profile])} : {};
        out.constructed=true;
        const steps=request.steps || request.frames.flatMap(frame=>{
            const parsed=JSON.parse(Buffer.from(frame,'base64').toString('utf8'));
            return [{op:'feed',bytes:frame},...(parsed.kind==='commit'?[]:[{op:'release'}])];
        }).concat([{op:'eof'},{op:'prepare'},{op:request.handoff?'borrow':'consume'}]);
        let ack;
        for(const step of steps) {
            let value;
            try {
                if(step.op==='feed') {value=gate.feed(Buffer.from(step.bytes,'base64'));if(value)ack=value;}
                else if(step.op==='release')value=gate.release_credit(ack);
                else if(step.op==='eof') {value=gate.end_input();ack=value;}
                else if(step.op==='prepare') {preparing=true;owner=gate.prepare();out.prepared_returned=true;value={prepared:true};preparing=false;}
                else if(step.op==='clock')clock=step.value;
                else if(step.op==='dispose_gate')gate.dispose();
                else if(step.op==='dispose_owner')owner.dispose();
                else if(step.op==='replace_public') {
                    let refused=false;try{owner.consume=()=>{badOwnerCalls++;};}catch{refused=true;}
                    value={replacement_refused:refused};
                }
                else if(step.op==='serialize') {
                    let refused=false;try{JSON.stringify(step.owner ? owner : gate);}catch{refused=true;}
                    value={serialization_refused:refused};
                } else if(step.op==='mutate_caller') {
                    request.manifest.files[0].host_text_sha256='0'.repeat(64);
                    request.profiles.private_compiler_declaration_text_preparation.production_enabled=true;
                    if(request.multiple)request.profiles.private_compiler_declaration_text_set_preparation.production_enabled=true;
                    if(request.handoff)request.profiles.private_compiler_declaration_text_handoff_preparation.production_enabled=true;
                    request.profiles.private_input_image_experiment.limits.capture_ms=0;
                } else if(step.op==='consume'||step.op==='borrow') {
                    let expected={...independentBorrowExpected,...step.expected_changes};
                    if(step.expected_delete)delete expected[step.expected_delete];
                    if(step.expected_kind==='getter')Object.defineProperty(expected,'context_sha256',{get(){expectedGetterCalls++;throw original;},enumerable:true,configurable:true});
                    if(step.expected_kind==='symbol')expected[Symbol('extra')]='extra';
                    if(step.expected_kind==='primitive')expected=7;
                    if(['reenter','throw'].includes(step.expected_kind))expected=new Proxy(expected,{ownKeys(target){
                        if(step.expected_kind==='throw')throw original;
                        try{_borrowPreparedCompilerTextSet(owner,independentBorrowExpected,()=>({}));}catch{/* Must poison outer borrow. */}
                        return Reflect.ownKeys(target);
                    }});
                    const callback=step.kind==='non_function' ? null : step.kind==='async' ? async ()=>{calls++;return {};} : row=>{
                        calls++;
                        if(step.kind==='throw')throw original;
                        if(step.kind==='thenable')return {then(){}};
                        if(step.kind==='getter')return Object.defineProperty({},'then',{get(){throw original;}});
                        if(step.kind==='expire')clock=deadline;
                        if(step.kind==='dispose_self')owner.dispose();
                        if(step.kind==='reenter') {try{owner.consume(()=>({}));}catch{/* Outer state must still refuse. */}}
                        if(step.kind==='reenter_private') {try{_borrowPreparedCompilerTextSet(owner,independentBorrowExpected,()=>({}));}catch{/* Must poison outer borrow. */}}
                        if(step.kind==='mutate_expected')expected.context_sha256='0'.repeat(64);
                        if(step.kind==='escape')escaped=row;
                        if(step.kind==='frozen_set')return {rows:project(row),array_frozen:Object.isFrozen(row),records_frozen:row.every(Object.isFrozen)};
                        return project(row);
                    };
                    let target=owner;
                    if(step.owner_selector==='clone')target={...owner,consume(){badOwnerCalls++;return {}},dispose(){badOwnerCalls++;}};
                    if(step.owner_selector==='snapshot')target=owner.snapshot();
                    if(step.owner_selector==='inherited')target=Object.create(owner);
                    if(step.owner_selector==='proxy')target=new Proxy(owner,{get(){badOwnerCalls++;throw original;}});
                    if(step.owner_selector==='primitive')target='private-owner-lookalike';
                    value=step.op==='borrow' ? _borrowPreparedCompilerTextSet(target,expected,callback) : owner.consume(callback);
                } else throw Error('unknown fixture action');
                out.events.push({op:step.op,value: value??null,snapshot:gate.snapshot(),prepared_snapshot:owner?.snapshot()??null});
            } catch(value) {
                preparing=false;out.events.push({op:step.op,...error(value),snapshot:gate.snapshot(),prepared_snapshot:owner?.snapshot()??null});
                if(!request.continue_after_error)break;
            }
        }
    }
} catch(value) {out.constructor_error=error(value);}
finally {
    preparing=false;Buffer.from=from;crypto.createHash=hash;
    phase?.dispose();gate?.dispose();owner?.dispose();
    out.final_snapshot=gate?.snapshot()??null;out.final_prepared_snapshot=owner?.snapshot()??null;
    out.forbidden_accesses=forbidden;out.compiler_image_calls=forbidden;out.consumer_calls=calls;
    out.copy_attempts=copyCount;out.files_finished=finishCount;
    out.bad_owner_method_calls=badOwnerCalls;out.expected_getter_calls=expectedGetterCalls;
    if(escaped)out.escaped_projection=project(escaped);
}
process.stdout.write(JSON.stringify(out));

'use strict';
// Explicit trusted first-party synthetic bodies only. No native/compiler loader.
const {compileFunction}=require('node:vm'),{performance}=require('node:perf_hooks'),{types}=require('node:util');
const crypto=require('node:crypto');
const {_borrowCompilerEntryText:borrow,_disposeCompilerEntry:dispose}=require('./compiler_entry_owner.cjs');
const {_canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate,StreamStagerRefusal}=require('./compiler_stream_stager.cjs');
const descriptors=Object.getOwnPropertyDescriptors,descriptor=Object.getOwnPropertyDescriptor,keys=Object.keys,ownKeys=Reflect.ownKeys;
const has=Object.hasOwn,freeze=Object.freeze,isFrozen=Object.isFrozen,prototype=Object.getPrototypeOf,plain=Object.prototype,apply=Reflect.apply;
const PROFILE='private_compiler_entry_evaluator',NODE_VERSION=process.version,active=new WeakMap();
const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
const hash=value=>sha(canonical(value)),bytes=value=>Buffer.byteLength(canonical(value),'utf8');
class CompilerEvaluatorRefusal extends Error {constructor(code){super(code);this.code=code;}}
const refuse=code=>{throw new CompilerEvaluatorRefusal(code);};
const need=(value,code)=>{if(!value)refuse(code);};
function dataCopy(value,code){
    need(value&&typeof value==='object'&&!Array.isArray(value)&&!types.isProxy(value),code);
    const props=descriptors(value);
    need(ownKeys(props).every(k=>typeof k==='string'&&has(props[k],'value')),code);
    return Object.fromEntries(keys(props).map(k=>[k,props[k].value]));
}
function checked(value,definition,schema,code){
    try{validate(value,definition,schema,code);}
    catch(error){if(error instanceof StreamStagerRefusal)refuse(error.code===code?code:'evaluator_policy_invalid');throw error;}
}
function safeSum(...values){const sum=values.reduce((a,b)=>a+b,0);need(values.every(n=>Number.isSafeInteger(n)&&n>=0)&&Number.isSafeInteger(sum),'evaluator_budget');return sum;}

function evaluateSyntheticCompilerEntry(owner,expectedInput,profilesInput,schemasInput,options){
    const previous=active.get(owner);
    if(previous){previous.poisoned=true;dispose(owner);refuse('evaluator_order');}
    let held=null,compiled=null,moduleRecord=null,exportsRecord=null,returned=null,observation=null,candidate=null,observe=null;
    const control={poisoned:false,dependency:false};let registered=false;
    try{
        need(owner!==null&&(typeof owner==='object'||typeof owner==='function'),'evaluator_order');active.set(owner,control);registered=true;
        const now=options?.now||(()=>performance.now()),deadline=options?.deadline;
        need(typeof now==='function','evaluator_deadline');let last=now();const started=last;
        need(Number.isFinite(started)&&Number.isFinite(deadline)&&started<deadline,'evaluator_deadline');
        const check=()=>{const time=now();need(!control.poisoned,'evaluator_order');need(Number.isFinite(time)&&time>=last&&time<deadline,'evaluator_deadline');last=time;};
        const expectedData=dataCopy(expectedInput,'evaluator_expected_invalid');
        expectedData.entry=dataCopy(expectedData.entry,'evaluator_expected_invalid');
        need(keys(expectedData).every(k=>k==='entry'||typeof expectedData[k]==='string')
            &&Object.values(expectedData.entry).every(v=>typeof v==='string'),'evaluator_expected_invalid');
        need(profilesInput&&typeof profilesInput==='object'&&profilesInput[PROFILE],'evaluator_policy_invalid');
        const first=profilesInput[PROFILE],entryProfile=profilesInput[first.entry_profile],capture=profilesInput[first.capture_profile],image=profilesInput[first.image_profile];
        need(entryProfile&&capture&&image?.limits&&Number.isSafeInteger(image.limits.metadata_bytes)&&image.limits.metadata_bytes>0,'evaluator_policy_invalid');
        const schemas=dataCopy(schemasInput,'evaluator_policy_invalid');
        need(keys(schemas).sort().join(',')==='entry,evaluator'&&schemas.entry?.$defs?.snapshot&&schemas.evaluator?.$defs?.policy,'evaluator_policy_invalid');
        const encoded=canonical({expected:expectedData,descriptor:first,entryProfile,capture,image,schemas});
        const inputBytes=Buffer.byteLength(encoded,'utf8');need(inputBytes<=image.limits.metadata_bytes,'evaluator_budget');
        held=JSON.parse(encoded);
        const {expected,descriptor:d,schemas:s}=held,limit=held.image.limits,schema=s.evaluator;
        checked(d,schema.$defs.policy,schema,'evaluator_policy_invalid');
        checked(expected,schema.$defs.expected,schema,'evaluator_expected_invalid');
        checked(held.entryProfile,s.entry.$defs.policy,s.entry,'evaluator_policy_invalid');
        need(d.version==='v1-private-synthetic-entry-scalar'&&d.model==='reviewed_synchronous_first_party_scalar_v1'
            &&d.transform==='identity_no_leading_bom_hashbang_v1'&&d.evaluation_scope==='reviewed_first_party_fixture_only'
            &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>d[k]===false)
            &&d.schema_sha256===hash(schema)&&d.entry_policy_sha256===hash(held.entryProfile)
            &&d.entry_schema_sha256===hash(s.entry)&&held.entryProfile.schema_sha256===hash(s.entry)
            &&d.capture_policy_sha256===hash(held.capture)&&d.image_policy_sha256===hash(held.image)
            &&held.entryProfile.capture_policy_sha256===d.capture_policy_sha256&&held.entryProfile.image_policy_sha256===d.image_policy_sha256,'evaluator_policy_invalid');
        need(expected.evaluator_policy_sha256===hash(d)&&expected.evaluator_schema_sha256===hash(schema)
            &&expected.entry.policy_sha256===d.entry_policy_sha256&&expected.entry.schema_sha256===d.entry_schema_sha256
            &&expected.parameters_sha256===hash(d.parameters)&&expected.options_sha256===hash(d.compile_options)
            &&expected.paths_sha256===hash(d.virtual_paths)&&expected.transform_sha256===hash(d.transform)
            &&expected.node_version===d.node_version&&NODE_VERSION===d.node_version,'evaluator_identity');
        need([limit.metadata_bytes,limit.image_bytes,limit.capture_ms].every(n=>Number.isSafeInteger(n)&&n>0),'evaluator_policy_invalid');
        need(deadline<=started+limit.capture_ms,'evaluator_deadline');check();
        const summary=(metrics,value)=>({version:d.version,status:d.result_status,entry:{...expected.entry},expected_sha256:hash(expected),
            evaluator_policy_sha256:expected.evaluator_policy_sha256,evaluator_schema_sha256:expected.evaluator_schema_sha256,
            owner_snapshot_sha256:expected.owner_snapshot_sha256,compile_input_sha256:expected.compile_input_sha256,
            parameters_sha256:expected.parameters_sha256,options_sha256:expected.options_sha256,transform_sha256:expected.transform_sha256,
            paths_sha256:expected.paths_sha256,result_sha256:expected.result_sha256,node_version:NODE_VERSION,observation:{value},...metrics,
            compilations:1,invocations:1,direct_dependency_requests:0,synthetic_entry_evaluated:true,entry_owner_disposed:true,
            compiler_loaded:false,loaded_code_attested:false,native_execution_attested:false,source_freshness_verified:false,
            snapshot_bound:false,image_admitted:false,production_enabled:false,semantic_lineage:false,cache_eligible:false,
            compiled_heap_bytes:null,runtime_heap_bytes:null,peak_rss_bytes:null});
        let metadataBytes=0,reservedBytes=0,totalReserved=0,entrySnapshot=null,compileBytes=0,compileUnits=0;
        borrow(owner,expected.entry,row=>{
            check();
            // This immutable observer is inspected only after the module-local
            // private borrow has admitted the exact real owner. It is not a brand.
            const method=descriptor(owner,'snapshot');
            need(isFrozen(owner)&&method&&has(method,'value')&&typeof method.value==='function'&&!method.writable&&!method.configurable,'evaluator_order');
            observe=method.value;entrySnapshot=apply(observe,owner,[]);
            checked(entrySnapshot,s.entry.$defs.snapshot,s.entry,'evaluator_identity');
            need(entrySnapshot.state==='BORROWING'&&entrySnapshot.code===''&&entrySnapshot.entry_input_verified&&entrySnapshot.entry_owner_registered
                &&hash(entrySnapshot)===expected.owner_snapshot_sha256,'evaluator_identity');
            need(typeof row.text==='string'&&row.raw_sha256===expected.entry.entry_sha256&&row.text_sha256===expected.entry.text_sha256
                &&sha(row.text)===expected.compile_input_sha256&&expected.compile_input_sha256===expected.entry.text_sha256,'evaluator_identity');
            need(!row.text.startsWith('\ufeff')&&!row.text.startsWith('#!'),'evaluator_text_refused');
            compileBytes=Buffer.byteLength(row.text,'utf8');compileUnits=row.text.length;
            need(compileBytes===row.size_bytes&&entrySnapshot.retained_entry_bytes===row.size_bytes
                &&entrySnapshot.retained_text_code_units===compileUnits,'evaluator_identity');
            metadataBytes=safeSum(inputBytes,bytes(entrySnapshot),bytes({parameters:d.parameters,compile_options:d.compile_options,virtual_paths:d.virtual_paths,transform:d.transform}));
            need(metadataBytes<=limit.metadata_bytes,'evaluator_budget');
            const ceiling=Number.MAX_SAFE_INTEGER;
            const placeholder=Object.fromEntries(['entry_capture_read_bytes','entry_metadata_bytes','entry_known_input_bytes','compile_text_bytes',
                'compile_text_code_units','evaluator_metadata_bytes','summary_reserved_bytes','known_inputs_reserved_bytes'].map(k=>[k,ceiling]));
            reservedBytes=bytes(summary(placeholder,ceiling));
            totalReserved=safeSum(entrySnapshot.known_owner_input_bytes,compileBytes,metadataBytes,reservedBytes);
            need(totalReserved<=limit.image_bytes&&reservedBytes<=limit.metadata_bytes,'evaluator_budget');check();
            const refusingRequire=freeze(function(){control.dependency=true;refuse('evaluator_dependency_refused');});
            moduleRecord=Object.create(null);exportsRecord=Object.create(null);moduleRecord.exports=exportsRecord;
            try{compiled=compileFunction(row.text,d.parameters,d.compile_options);}
            catch(error){if(error instanceof SyntaxError)refuse('evaluator_syntax_refused');throw error;}
            check();
            returned=apply(compiled,exportsRecord,[exportsRecord,refusingRequire,moduleRecord,d.virtual_paths.filename,d.virtual_paths.dirname]);
            need(!control.dependency,'evaluator_dependency_refused');check();
            need(returned===undefined,'evaluator_result_invalid');
            const exported=descriptor(moduleRecord,'exports');
            need(exported&&has(exported,'value'),'evaluator_result_invalid');
            const value=exported.value;
            need(value&&typeof value==='object'&&!Array.isArray(value)&&!types.isProxy(value)
                &&(prototype(value)===null||prototype(value)===plain),'evaluator_result_invalid');
            const data=dataCopy(value,'evaluator_result_invalid');
            checked(data,schema.$defs.observation,schema,'evaluator_result_invalid');
            need(hash(data)===expected.result_sha256,'evaluator_identity');
            observation={value:data.value};compiled=moduleRecord=exportsRecord=returned=null;check();
        });
        check();
        const after=apply(observe,owner,[]);
        need(after.state==='DISPOSED'&&after.retained_entry_bytes===0&&after.retained_text_code_units===0
            &&after.open_file_handles===0&&!after.entry_owner_registered&&!after.entry_input_verified,'evaluator_order');
        const metrics={entry_capture_read_bytes:entrySnapshot.capture_read_bytes,entry_metadata_bytes:entrySnapshot.metadata_bytes,
            entry_known_input_bytes:entrySnapshot.known_owner_input_bytes,compile_text_bytes:compileBytes,compile_text_code_units:compileUnits,
            evaluator_metadata_bytes:metadataBytes,summary_reserved_bytes:reservedBytes,known_inputs_reserved_bytes:totalReserved};
        candidate=summary(metrics,observation.value);
        checked(candidate,schema.$defs.result,schema,'evaluator_result_invalid');need(bytes(candidate)<=reservedBytes,'evaluator_budget');
        freeze(candidate.entry);freeze(candidate.observation);freeze(candidate);check();return candidate;
    }finally{
        dispose(owner);if(registered)active.delete(owner);
        held=compiled=moduleRecord=exportsRecord=returned=observation=candidate=observe=null;
    }
}

module.exports={evaluateSyntheticCompilerEntry,CompilerEvaluatorRefusal};

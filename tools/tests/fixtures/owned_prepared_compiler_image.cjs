'use strict';
// Synthetic in-process stream frames and compiler API double, never TypeScript.
const fs=require('node:fs'),Module=require('node:module');
const request=JSON.parse(fs.readFileSync(0,'utf8'));
const {performance}=require('node:perf_hooks');
const image=require('../../core/ts_input_image_experiment.cjs');
const producer=require('../../core/compiler_declaration_text.cjs');
let factory=producer.createCompilerDeclarationTextHandoffPreparation;
if(request.owner_kind==='foreign') {
    const filename=require.resolve('../../core/compiler_declaration_text.cjs'),foreign=new Module(filename);
    foreign.filename=filename;foreign.paths=module.paths;foreign._compile(fs.readFileSync(filename,'utf8'),filename);
    factory=foreign.exports.createCompilerDeclarationTextHandoffPreparation;
} else if(request.owner_kind==='legacy') factory=producer.createCompilerDeclarationTextSetPreparation;
let forbidden=0,configCalls=0,preprocessCalls=0,programCalls=0,decodeCalls=0,constructing=false,publicCalls=0;
let gate,owner,token,instance,captured,candidateDisposed=0,afterCandidate=0,publication=false,clock=0;
const original=new RangeError('controlled image fixture failure'),freeze=Object.freeze,Decoder=global.TextDecoder;
const deny=()=>{forbidden++;throw Error('forbidden dependency');},load=Module._load;
Module._load=function(name,...args){
    if(name!=='./compiler_declaration_text.cjs')return deny();
    return load.call(this,name,...args);
};
for(const key of ['readFileSync','readFile','openSync','open','statSync','stat','readdirSync','readdir','writeFileSync','writeFile'])fs[key]=deny;
global.TextDecoder=class extends Decoder {decode(...args){if(constructing)decodeCalls++;return super.decode(...args);}};
const deadline=request.real_clock?performance.now()+1000:1000;
const now=()=>{
    if(captured){
        afterCandidate++;
        if(request.fault==='producer_post_deadline')return deadline;
        if(request.fault==='wrapper_post_deadline'&&afterCandidate>=2)return deadline;
    }
    if(publication&&request.fault==='publication_deadline')return deadline;
    if(request.fault==='clock_throw'&&constructing)throw original;
    return request.real_clock?performance.now():clock;
};
Object.freeze=function(value){
    if(constructing&&value?.replay&&value?.summary?.image_sha256&&!value.summary.prepared_ingress_sha256){
        captured=value;
        const dispose=value.dispose;value.dispose=()=>{candidateDisposed++;return dispose();};
        if(request.fault==='candidate_allocation_error')throw original;
    }
    if(constructing&&value?.replay&&value?.summary?.prepared_ingress_sha256){
        publication=true;if(request.fault==='publication_allocation_error')throw original;
    }
    return freeze(value);
};
const rawInputs={entries:request.entries.map(row=>({path:row.path,role:row.role,raw:Buffer.from(row.raw_base64,'base64')})),namespace_complete:true};
const tsDouble={version:request.binding.compiler_version,
    createProgram(){programCalls++;throw Error('Program forbidden');},
    parseConfigFileTextToJson(path,text){
        configCalls++;
        if(request.fault==='config_throw')throw original;
        if(request.fault==='dispose_during_config')owner.dispose();
        if(request.fault==='mutate_callers'){
            request.binding.atlas_sha256='0'.repeat(64);request.policy.limits.image_bytes=1;
            request.admission.expected.binding_sha256='0'.repeat(64);rawInputs.entries.forEach(row=>row.raw.fill(120));
        }
        try{return {config:JSON.parse(text)};}catch{return {error:true};}
    },
    preProcessFile(){
        preprocessCalls++;if(request.fault==='preprocess_throw')throw original;
        return {typeReferenceDirectives:request.fault==='type_reference'?[{}]:[],
            importedFiles:request.fault==='external_import'?[{fileName:'external'}]:[],referencedFiles:[]};
    }
};
let result;
try{
    const textSchema=request.owner_kind==='legacy'?request.legacy_schema:request.text_schema;
    gate=factory(request.expected,request.manifest,request.profiles,request.transport_schema,textSchema,{deadline,now});
    for(const encoded of request.frames){
        const frame=Buffer.from(encoded,'base64'),ack=gate.feed(frame);
        if(JSON.parse(frame.toString('utf8')).kind!=='commit')gate.release_credit(ack);
    }
    gate.end_input();owner=gate.prepare();token=owner;
    if(request.owner_kind==='clone')token={...owner};
    if(request.owner_kind==='proxy')token=new Proxy(owner,{get(){publicCalls++;throw original;}});
    if(request.owner_kind==='fake')token={consume(){publicCalls++;throw original;},dispose(){publicCalls++;throw original;}};
    if(request.owner_kind==='consumed')owner.consume(()=>null);
    if(request.owner_kind==='disposed')owner.dispose();
    if(request.fault==='dispose_private'){
        const first=producer._disposePreparedCompilerTextSet(owner),second=producer._disposePreparedCompilerTextSet(owner);
        result={status:'PRIVATE_DISPOSED',first,second};
    }else{
        if(request.fault==='invalid_deadline')clock=deadline;
        constructing=true;
        instance=image.createPreparedCompilerExperiment(tsDouble,rawInputs,request.binding,request.policy,token,request.admission,
            {deadline,now});
        let serializationRefused=false;try{JSON.stringify(instance);}catch{serializationRefused=true;}
        result={status:'CONSTRUCTED_WITH_COMPILER_API_DOUBLE',summary:instance.summary,serialization_refused:serializationRefused};
    }
}catch(error){result={status:'REFUSED',error:error.code||(error===original?'injected_original':error.name),original_identity:error===original};}
finally{
    constructing=false;
    result.owner_snapshot=owner?.snapshot();result.candidate_disposals_before_fixture=candidateDisposed;
    if(captured&&result.status==='REFUSED'){
        try{captured.replay(request.binding);result.candidate_rollback='NOT_DISPOSED';}
        catch(error){result.candidate_rollback=error.code||error.name;}
    }
    instance?.dispose();owner?.dispose();gate?.dispose();
}
process.stdout.write(JSON.stringify({...result,node_version:process.version,api_double:true,constructor_calls:result.status==='PRIVATE_DISPOSED'?0:1,
    config_double_calls:configCalls,preprocess_double_calls:preprocessCalls,program_calls:programCalls,
    constructor_decode_calls:decodeCalls,forbidden_accesses:forbidden,typescript_module_loads:0,public_owner_calls:publicCalls})+'\n');

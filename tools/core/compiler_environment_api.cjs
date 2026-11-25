'use strict';
// Reviewed synchronous first-party fixtures only. VM is not a hostile-code sandbox.
const {createContext,compileFunction,isContext}=require('node:vm');
const {performance}=require('node:perf_hooks'),{types}=require('node:util'),crypto=require('node:crypto');
const {_borrowCompilerEntryText:borrow,_disposeCompilerEntry:disposeEntry}=require('./compiler_entry_owner.cjs');
const {_canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate,StreamStagerRefusal}=require('./compiler_stream_stager.cjs');
const descriptor=Object.getOwnPropertyDescriptor,descriptors=Object.getOwnPropertyDescriptors,ownKeys=Reflect.ownKeys;
const has=Object.hasOwn,freeze=Object.freeze,create=Object.create,define=Object.defineProperty,apply=Reflect.apply;
const prototype=Object.getPrototypeOf,plain=Object.prototype,isFrozen=Object.isFrozen;
const PROFILE='private_compiler_environment_api',NODE_VERSION=process.version,active=new WeakMap(),apis=new WeakMap(),failureStages=new WeakMap();
const ACCESSOR_PROFILE='private_compiler_accessor_bridge';
const INVENTORY_PROFILE='private_compiler_export_inventory',extensible=Object.isExtensible,wellFormed=String.prototype.isWellFormed;
// Finite implementation gate; canonical policy still owns the complete manifest.
const INVENTORY_MANIFEST='8371b8a5f67821e10c5f4b9b65419c46db01eb3ff716aa656f73465a86e263be';
const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
const hash=value=>sha(canonical(value)),bytes=value=>Buffer.byteLength(canonical(value),'utf8');
class CompilerEnvironmentRefusal extends Error {constructor(code){super(code);this.code=code;}}
const refuse=code=>{throw new CompilerEnvironmentRefusal(code);};
const need=(value,code)=>{if(!value)refuse(code);};
const object=value=>value!==null&&(typeof value==='object'||typeof value==='function');
function copy(value,code,seen=new Set()){
    if(value===null||['string','boolean','number'].includes(typeof value))return value;
    need(value&&typeof value==='object'&&!types.isProxy(value)&&!seen.has(value),code);
    need(Array.isArray(value)||prototype(value)===null||prototype(value)===plain,code);
    seen.add(value);
    try{
        const fields=descriptors(value),keys=ownKeys(fields);
        need(keys.every(k=>typeof k==='string'&&has(fields[k],'value')),code);
        if(Array.isArray(value)){
            const length=fields.length?.value;
            need(Number.isSafeInteger(length)&&length>=0&&keys.length===length+1,code);
            return Array.from({length},(_,i)=>{need(has(fields,String(i)),code);return copy(fields[i].value,code,seen);});
        }
        return Object.fromEntries(keys.map(k=>[k,copy(fields[k].value,code,seen)]));
    }finally{seen.delete(value);}
}
function checked(value,definition,schema,code){
    try{validate(value,definition,schema,code);}
    catch(error){if(error instanceof StreamStagerRefusal)refuse(error.code===code?code:'environment_policy_invalid');throw error;}
}
function dataField(value,key){
    need(value&&typeof value==='object'&&!types.isProxy(value),'environment_policy_invalid');
    const field=descriptor(value,key);need(field&&has(field,'value'),'environment_policy_invalid');return field.value;
}
function sum(...values){const n=values.reduce((a,b)=>a+b,0);need(values.every(v=>Number.isSafeInteger(v)&&v>=0)&&Number.isSafeInteger(n),'environment_budget');return n;}
function note(error,stage){if(object(error)&&!failureStages.has(error))failureStages.set(error,stage);return error;}
function _compilerEnvironmentFailureStage(error){return object(error)?failureStages.get(error)||'unknown':'unknown';}
function _disposeCompilerEnvironmentApi(token){
    const row=apis.get(token);if(!row)return false;
    row.fn=row.context=null;row.state='DISPOSED';apis.delete(token);return true;
}
function _consumeCompilerEnvironmentApi(token,expectedInput){
    const row=apis.get(token);need(row,'environment_order');
    if(row.state!=='HELD'){row.control.poisoned=true;refuse('environment_order');}
    row.state='CONSUMING';
    try{
        need(hash(copy(expectedInput,'environment_expected_invalid'))===row.expectedHash,'environment_identity');
        row.check();need(!row.control.poisoned&&row.state==='CONSUMING'&&row.fn!==null,'environment_order');
        row.control.stage='api';row.control.apiCalls++;
        const value=apply(row.fn,undefined,[]);
        row.control.stage='projection';row.check();
        need(row.state==='CONSUMING'&&row.fn!==null,'environment_order');
        need(Number.isSafeInteger(value)&&value>=0,'environment_result_invalid');
        return value;
    }catch(error){row.control.poisoned=true;throw note(error,row.control.stage);}
    finally{_disposeCompilerEnvironmentApi(token);}
}

function inventoryGraph(graph,selection,expected,schema,check){
    let keys=null,rows=null,selected=null;
    try{
        // This is a reviewed ordinary-fixture gate, not a universal exotic detector.
        need(graph&&typeof graph==='object'&&!types.isProxy(graph),'environment_inventory_refused');
        need(!Array.isArray(graph)&&prototype(graph)===null&&!extensible(graph),'environment_inventory_refused');check();
        keys=ownKeys(graph);check();
        need(keys.length===selection.manifest.length,'environment_inventory_refused');
        let nameBytes=0;
        for(const key of keys){
            need(typeof key==='string'&&apply(wellFormed,key,[]),'environment_inventory_refused');
            nameBytes=sum(nameBytes,Buffer.byteLength(key,'utf8'));
            need(nameBytes<=selection.names_utf8_bytes,'environment_budget');
        }
        keys.sort((a,b)=>Buffer.compare(Buffer.from(a,'utf8'),Buffer.from(b,'utf8')));
        need(new Set(keys).size===keys.length&&hash(keys)===expected.inventory_names_sha256,'environment_inventory_refused');
        rows=[];
        for(const name of keys){
            check();const field=descriptor(graph,name);check();
            need(field&&!field.configurable,'environment_inventory_refused');
            const data=has(field,'value');need(!data||field.writable===false,'environment_inventory_refused');
            const row={name,descriptor_kind:data?'data':'accessor',enumerable:field.enumerable,configurable:field.configurable,
                writable:data?field.writable:null,getter_present:!data&&field.get!==undefined,setter_present:!data&&field.set!==undefined,
                data_kind:data?(field.value===null?'null':typeof field.value):null,selected:name==='readFixtureValue'};
            checked(row,schema.$defs.inventory_row,schema,'environment_inventory_refused');rows.push(row);
            need(bytes(rows)<=selection.rows_utf8_bytes,'environment_budget');
            if(row.selected)selected=field;
        }
        const rowBytes=bytes(rows),digest=hash(rows);
        need(nameBytes===selection.names_utf8_bytes&&rowBytes===selection.rows_utf8_bytes
            &&digest===expected.inventory_manifest_sha256&&digest===expected.inventory_rows_sha256,'environment_inventory_refused');check();
        return {selected,count:rows.length,nameBytes,rowBytes};
    }finally{keys=rows=selected=graph=null;}
}

function evaluateEnvironment(mode,owner,expectedInput,profilesInput,schemasInput,options){
    const inventory=mode==='inventory',accessorBridge=mode!==false;
    const existing=active.get(owner);
    if(existing){existing.poisoned=true;disposeEntry(owner);refuse('environment_order');}
    const control={poisoned:false,dependency:false,stage:'validation',bridgeCalls:0,getterCalls:0,apiCalls:0};
    let registered=false,inBorrow=false,held=null,context=null,moduleRecord=null,exportsRecord=null,compiled=null,observe=null,token=null,candidate=null;
    let primary=null,hasPrimary=false;
    try{
        need(object(owner),'environment_order');active.set(owner,control);registered=true;
        const now=options?.now||(()=>performance.now()),deadline=options?.deadline;
        need(typeof now==='function','environment_deadline');const started=now();let last=started;
        need(Number.isFinite(started)&&Number.isFinite(deadline)&&started<deadline,'environment_deadline');
        const check=()=>{const time=now();need(!control.poisoned,'environment_order');need(!control.dependency,'environment_dependency_refused');
            need(Number.isFinite(time)&&time>=last&&time<deadline,'environment_deadline');last=time;
            if(inBorrow&&observe){const state=apply(observe,owner,[]);need(state.state==='BORROWING'&&state.entry_owner_registered&&state.entry_input_verified,'environment_order');}};
        const d=copy(dataField(profilesInput,inventory?INVENTORY_PROFILE:accessorBridge?ACCESSOR_PROFILE:PROFILE),'environment_policy_invalid'),schemas=copy(schemasInput,'environment_policy_invalid');
        const expected=copy(expectedInput,'environment_expected_invalid');
        need(d&&Object.keys(schemas).sort().join(',')==='entry,environment','environment_policy_invalid');
        const entryProfile=copy(dataField(profilesInput,d.entry_profile),'environment_policy_invalid'),
            capture=copy(dataField(profilesInput,d.capture_profile),'environment_policy_invalid'),image=copy(dataField(profilesInput,d.image_profile),'environment_policy_invalid');
        need(entryProfile&&capture&&image?.limits&&schemas.entry?.$defs?.snapshot&&schemas.environment?.$defs?.policy,'environment_policy_invalid');
        const encoded=canonical({expected,descriptor:d,entryProfile,capture,image,schemas});
        const inputBytes=Buffer.byteLength(encoded,'utf8');need(inputBytes<=image.limits.metadata_bytes,'environment_budget');
        held=JSON.parse(encoded);
        const {descriptor:policy,schemas:s}=held,limit=held.image.limits,schema=s.environment;
        checked(policy,schema.$defs.policy,schema,'environment_policy_invalid');
        checked(held.expected,schema.$defs.expected,schema,'environment_expected_invalid');
        checked(held.entryProfile,s.entry.$defs.policy,s.entry,'environment_policy_invalid');
        need(policy.version===(inventory?'v1-private-synthetic-export-inventory':accessorBridge?'v1-private-synthetic-accessor-bridge':'v1-private-synthetic-environment-api')
            &&policy.model===(inventory?'reviewed_frozen_synthetic_export_inventory_v1':accessorBridge?'reviewed_synchronous_synthetic_accessor_bridge_v1':'reviewed_synchronous_synthetic_getter_api_v1')
            &&policy.transform==='identity_no_leading_bom_hashbang_v1'&&policy.consumer==='fixed_synthetic_scalar_consumer_v1'
            &&policy.context_options.codeGeneration.strings===false&&policy.context_options.codeGeneration.wasm===false
            &&policy.api_selection.operation==='read_fixture_value'&&policy.api_selection.arguments.length===0
            &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>policy[k]===false)
            &&policy.schema_sha256===hash(schema)&&policy.entry_policy_sha256===hash(held.entryProfile)
            &&policy.entry_schema_sha256===hash(s.entry)&&held.entryProfile.schema_sha256===hash(s.entry)
            &&policy.capture_policy_sha256===hash(held.capture)&&policy.image_policy_sha256===hash(held.image)
            &&held.entryProfile.capture_policy_sha256===policy.capture_policy_sha256&&held.entryProfile.image_policy_sha256===policy.image_policy_sha256,'environment_policy_invalid');
        if(accessorBridge){
            const bridge=policy.bridge_selection;
            need(policy.api_selection.bridge==='own_getter'&&bridge.invocations===1&&bridge.receiver==='internal_module_record'
                &&bridge.descriptor.property==='exports'&&bridge.descriptor.kind==='own_getter_no_setter'
                &&bridge.descriptor.configurable===false&&bridge.descriptor.enumerable===false&&bridge.descriptor.setter_present===false,'environment_policy_invalid');
        }
        let inventoryReserved=0,inventoryFacts=null;
        if(inventory){
            const selection=policy.inventory_selection,manifest=selection.manifest;
            need(policy.bridge_selection.result==='frozen_ordinary_export_inventory'&&policy.api_selection.export_property==='readFixtureValue'
                &&policy.api_selection.getter==='own_getter_no_setter'&&selection.observer==='own_descriptor_rows_utf8_v1'
                &&selection.graph==='reviewed_frozen_ordinary_null_prototype'&&hash(manifest)===INVENTORY_MANIFEST
                &&selection.names_utf8_bytes===manifest.reduce((n,r)=>sum(n,Buffer.byteLength(r.name,'utf8')),0)
                &&selection.rows_utf8_bytes===bytes(manifest),'environment_policy_invalid');
            inventoryReserved=sum(selection.rows_utf8_bytes,selection.rows_utf8_bytes,
                selection.names_utf8_bytes,selection.names_utf8_bytes,selection.names_utf8_bytes);
        }
        const e=held.expected;
        const pinValues={environment_policy_sha256:hash(policy),environment_schema_sha256:hash(schema),
            parameters_sha256:hash(policy.parameters),compile_options_sha256:hash(policy.compile_options),context_options_sha256:hash(policy.context_options),
            denied_globals_sha256:hash(policy.denied_globals),api_selection_sha256:hash(policy.api_selection),consumer_sha256:hash(policy.consumer),
            transform_sha256:hash(policy.transform),paths_sha256:hash(policy.virtual_paths),
            ...(accessorBridge?{bridge_selection_sha256:hash(policy.bridge_selection),bridge_descriptor_sha256:hash(policy.bridge_selection.descriptor)}:{}),
            ...(inventory?{inventory_selection_sha256:hash(policy.inventory_selection),inventory_manifest_sha256:hash(policy.inventory_selection.manifest),
                inventory_names_sha256:hash(policy.inventory_selection.manifest.map(r=>r.name)),inventory_rows_sha256:hash(policy.inventory_selection.manifest)}:{})};
        need(Object.entries(pinValues).every(([k,v])=>e[k]===v)&&e.entry.policy_sha256===policy.entry_policy_sha256
            &&e.entry.schema_sha256===policy.entry_schema_sha256&&e.node_version===policy.node_version&&NODE_VERSION===policy.node_version,'environment_identity');
        need([limit.metadata_bytes,limit.image_bytes,limit.capture_ms].every(n=>Number.isSafeInteger(n)&&n>0),'environment_policy_invalid');
        need(deadline<=started+limit.capture_ms,'environment_deadline');check();
        let entrySnapshot=null,compileBytes=0,compileUnits=0,metadataBytes=0,reservedBytes=0,totalReserved=0,value;
        const summary=(metrics,scalar)=>({version:policy.version,status:policy.result_status,entry:{...e.entry},expected_sha256:hash(e),...pinValues,
            owner_snapshot_sha256:e.owner_snapshot_sha256,compile_input_sha256:e.compile_input_sha256,result_sha256:e.result_sha256,node_version:NODE_VERSION,
            observation:{value:scalar},...metrics,environment_contexts_created:1,compilations:1,entry_invocations:1,getter_invocations:1,api_invocations:1,
            ...(accessorBridge?{bridge_invocations:1,synthetic_accessor_bridge_observed:true}:{}),
            ...(inventory?{inventory_count:policy.inventory_selection.manifest.length,inventory_getter_invocations:0,inventory_api_invocations:0,
                inventory_reserved_bytes:inventoryReserved,inventory_name_bytes:policy.inventory_selection.names_utf8_bytes,
                inventory_row_bytes:policy.inventory_selection.rows_utf8_bytes,synthetic_export_inventory_observed:true,inventory_refs_cleared:true}:{}),
            direct_dependency_requests:0,synthetic_environment_api_observed:true,entry_owner_disposed:true,api_owner_disposed:true,local_environment_refs_cleared:true,
            compiler_loaded:false,loaded_code_attested:false,native_execution_attested:false,source_freshness_verified:false,snapshot_bound:false,image_admitted:false,
            production_enabled:false,semantic_lineage:false,cache_eligible:false,compiled_heap_bytes:null,runtime_heap_bytes:null,peak_rss_bytes:null});
        borrow(owner,e.entry,row=>{
            inBorrow=true;
            control.stage='entry_identity';check();
            const method=descriptor(owner,'snapshot');
            need(isFrozen(owner)&&method&&has(method,'value')&&typeof method.value==='function'&&!method.writable&&!method.configurable,'environment_order');
            observe=method.value;entrySnapshot=apply(observe,owner,[]);
            checked(entrySnapshot,s.entry.$defs.snapshot,s.entry,'environment_identity');
            need(entrySnapshot.state==='BORROWING'&&entrySnapshot.code===''&&entrySnapshot.entry_input_verified&&entrySnapshot.entry_owner_registered
                &&hash(entrySnapshot)===e.owner_snapshot_sha256,'environment_identity');
            need(typeof row.text==='string'&&row.raw_sha256===e.entry.entry_sha256&&row.text_sha256===e.entry.text_sha256
                &&sha(row.text)===e.compile_input_sha256&&e.compile_input_sha256===e.entry.text_sha256,'environment_identity');
            need(!row.text.startsWith('\ufeff')&&!row.text.startsWith('#!'),'environment_text_refused');
            compileBytes=Buffer.byteLength(row.text,'utf8');compileUnits=row.text.length;
            need(compileBytes===row.size_bytes&&entrySnapshot.retained_entry_bytes===row.size_bytes
                &&entrySnapshot.retained_text_code_units===compileUnits,'environment_identity');
            metadataBytes=sum(inputBytes,bytes(entrySnapshot),bytes({parameters:policy.parameters,compile_options:policy.compile_options,
                context_options:policy.context_options,denied_globals:policy.denied_globals,api_selection:policy.api_selection,consumer:policy.consumer,
                virtual_paths:policy.virtual_paths,transform:policy.transform,...(accessorBridge?{bridge_selection:policy.bridge_selection}:{}),
                ...(inventory?{inventory_selection:policy.inventory_selection}:{})}),inventoryReserved);
            need(metadataBytes<=limit.metadata_bytes,'environment_budget');
            const max=Number.MAX_SAFE_INTEGER;
            const placeholder=Object.fromEntries(['entry_capture_read_bytes','entry_metadata_bytes','entry_known_input_bytes','compile_text_bytes',
                'compile_text_code_units','environment_metadata_bytes','summary_reserved_bytes','known_inputs_reserved_bytes'].map(k=>[k,max]));
            reservedBytes=bytes(summary(placeholder,max));totalReserved=sum(entrySnapshot.known_owner_input_bytes,compileBytes,metadataBytes,reservedBytes);
            need(totalReserved<=limit.image_bytes&&reservedBytes<=limit.metadata_bytes,'environment_budget');check();
            control.stage='context';
            const globals=create(null);
            for(const key of policy.denied_globals)define(globals,key,{value:undefined,writable:false,configurable:false,enumerable:false});
            context=createContext(globals,policy.context_options);
            need(isContext(context),'environment_context_refused');
            const checkGlobals=()=>{for(const key of policy.denied_globals){const prop=descriptor(context,key);
                need(prop&&has(prop,'value')&&prop.value===undefined&&!prop.writable&&!prop.configurable,'environment_context_refused');}};
            checkGlobals();check();
            const refusingRequire=freeze(function(){control.dependency=true;refuse('environment_dependency_refused');});
            moduleRecord=create(null);exportsRecord=create(null);moduleRecord.exports=exportsRecord;
            control.stage='compile';compiled=compileFunction(row.text,policy.parameters,{...policy.compile_options,parsingContext:context});check();
            control.stage='entry';const returned=apply(compiled,exportsRecord,[exportsRecord,refusingRequire,moduleRecord,policy.virtual_paths.filename,policy.virtual_paths.dirname]);
            check();need(returned===undefined,'environment_export_refused');checkGlobals();
            const bridge=descriptor(moduleRecord,'exports');let graph;
            if(accessorBridge){
                control.stage='bridge_selection';
                need(bridge&&!has(bridge,'value')&&typeof bridge.get==='function'&&bridge.set===undefined
                    &&!types.isProxy(bridge.get)&&!types.isAsyncFunction(bridge.get)&&!types.isGeneratorFunction(bridge.get),'environment_bridge_refused');
                const shape={property:'exports',kind:'own_getter_no_setter',configurable:bridge.configurable,enumerable:bridge.enumerable,setter_present:bridge.set!==undefined};
                need(hash(shape)===e.bridge_descriptor_sha256,'environment_bridge_refused');
                control.stage='bridge';control.bridgeCalls++;graph=apply(bridge.get,moduleRecord,[]);check();checkGlobals();
                const after=descriptor(moduleRecord,'exports');
                need(after&&after.get===bridge.get&&after.set===undefined&&!after.configurable&&!after.enumerable,'environment_bridge_refused');
                need(control.bridgeCalls===1,'environment_order');control.stage='exports';
            }else{need(bridge&&has(bridge,'value'),'environment_export_refused');graph=bridge.value;}
            let selected;
            if(inventory){
                control.stage='inventory';check();inventoryFacts=inventoryGraph(graph,policy.inventory_selection,e,schema,check);
                selected=inventoryFacts.selected;inventoryFacts=null;check();checkGlobals();
            }else{
                need(graph&&typeof graph==='object'&&!Array.isArray(graph)&&!types.isProxy(graph),'environment_export_refused');
                const keys=ownKeys(graph);need(keys.length===1&&keys[0]===policy.api_selection.export_property,'environment_export_refused');
                selected=descriptor(graph,policy.api_selection.export_property);
            }
            need(selected&&!has(selected,'value')&&typeof selected.get==='function'&&selected.set===undefined
                &&!types.isProxy(selected.get)&&!types.isAsyncFunction(selected.get)&&!types.isGeneratorFunction(selected.get),'environment_export_refused');
            control.stage='getter';control.getterCalls++;const fn=apply(selected.get,graph,[]);check();
            need(typeof fn==='function'&&!types.isProxy(fn)&&!types.isAsyncFunction(fn)&&!types.isGeneratorFunction(fn),'environment_api_refused');
            token=freeze({kind:'private_synthetic_compiler_api'});
            const apiExpected={entry:e.entry,environment_policy_sha256:e.environment_policy_sha256,environment_schema_sha256:e.environment_schema_sha256,
                api_selection_sha256:e.api_selection_sha256,operation:policy.api_selection.operation,consumer_sha256:e.consumer_sha256,
                ...(accessorBridge?{bridge_selection_sha256:e.bridge_selection_sha256,bridge_descriptor_sha256:e.bridge_descriptor_sha256}:{}),
                ...(inventory?{inventory_selection_sha256:e.inventory_selection_sha256,inventory_manifest_sha256:e.inventory_manifest_sha256,
                    inventory_names_sha256:e.inventory_names_sha256,inventory_rows_sha256:e.inventory_rows_sha256}:{})};
            apis.set(token,{state:'HELD',fn,context,control,check,expectedHash:hash(apiExpected)});
            control.stage='api_ready';check();value=_consumeCompilerEnvironmentApi(token,apiExpected);
            need(!apis.has(token)&&control.getterCalls===1&&control.apiCalls===1,'environment_order');checkGlobals();
            control.stage='projection';need(hash({value})===e.result_sha256,'environment_identity');check();
            selected=graph=null;compiled=moduleRecord=exportsRecord=context=null;
        });
        inBorrow=false;
        control.stage='settlement';check();
        const after=apply(observe,owner,[]);
        need(after.state==='DISPOSED'&&after.retained_entry_bytes===0&&after.retained_text_code_units===0
            &&after.open_file_handles===0&&!after.entry_owner_registered&&!after.entry_input_verified&&!apis.has(token),'environment_order');
        const metrics={entry_capture_read_bytes:entrySnapshot.capture_read_bytes,entry_metadata_bytes:entrySnapshot.metadata_bytes,
            entry_known_input_bytes:entrySnapshot.known_owner_input_bytes,compile_text_bytes:compileBytes,compile_text_code_units:compileUnits,
            environment_metadata_bytes:metadataBytes,summary_reserved_bytes:reservedBytes,known_inputs_reserved_bytes:totalReserved};
        candidate=summary(metrics,value);checked(candidate,schema.$defs.result,schema,'environment_result_invalid');
        need(bytes(candidate)<=reservedBytes,'environment_budget');freeze(candidate.entry);freeze(candidate.observation);freeze(candidate);check();
        control.stage='cleanup';
        _disposeCompilerEnvironmentApi(token);disposeEntry(owner);
        held=compiled=moduleRecord=exportsRecord=context=observe=token=null;
        control.stage='final';check();
    }catch(error){primary=note(error,control.stage);hasPrimary=true;}
    finally{
        const cleanup=[];
        try{_disposeCompilerEnvironmentApi(token);}catch(error){cleanup.push(note(error,'cleanup'));}
        try{disposeEntry(owner);}catch(error){cleanup.push(note(error,'cleanup'));}
        held=compiled=moduleRecord=exportsRecord=context=observe=token=null;
        if(registered)active.delete(owner);
        if(cleanup.length){primary=note(new AggregateError(hasPrimary?[primary,...cleanup]:cleanup,'compiler_environment_cleanup_failed',hasPrimary?{cause:primary}:undefined),'cleanup');hasPrimary=true;}
    }
    if(hasPrimary)throw primary;
    return candidate;
}

function evaluateSyntheticCompilerEnvironment(owner,expected,profiles,schemas,options){
    return evaluateEnvironment(false,owner,expected,profiles,schemas,options);
}
function evaluateSyntheticCompilerAccessorEnvironment(owner,expected,profiles,schemas,options){
    return evaluateEnvironment(true,owner,expected,profiles,schemas,options);
}
function evaluateSyntheticCompilerInventoryEnvironment(owner,expected,profiles,schemas,options){
    return evaluateEnvironment('inventory',owner,expected,profiles,schemas,options);
}

module.exports={evaluateSyntheticCompilerEnvironment,evaluateSyntheticCompilerAccessorEnvironment,evaluateSyntheticCompilerInventoryEnvironment,_consumeCompilerEnvironmentApi,_disposeCompilerEnvironmentApi,
    _compilerEnvironmentFailureStage,CompilerEnvironmentRefusal};

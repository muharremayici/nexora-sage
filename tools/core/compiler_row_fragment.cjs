'use strict';
// Disabled supplied-text observation. No file owner, callback, evaluator or default caller.
const crypto=require('node:crypto'),{performance}=require('node:perf_hooks'),{types}=require('node:util');
const {recognizeRowFragment,CompilerExportRecipeRefusal}=require('./compiler_source_recipe_cursor.cjs');
const {copy}=require('./compiler_text_window.cjs');
const {_canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate,StreamStagerRefusal}=require('./compiler_stream_stager.cjs');
const PROFILE='private_compiler_row_fragment';
const sha=value=>crypto.createHash('sha256').update(value).digest('hex'),hash=value=>sha(canonical(value)),bytes=value=>Buffer.byteLength(canonical(value),'utf8');
const stages=new WeakMap();
class CompilerRowFragmentRefusal extends Error{constructor(code){super(code);this.code=code;}}
const need=(value,code)=>{if(!value)throw new CompilerRowFragmentRefusal(code);};
function field(value,key){need(value&&typeof value==='object'&&!types.isProxy(value),'row_policy_invalid');const d=Object.getOwnPropertyDescriptor(value,key);need(d&&Object.hasOwn(d,'value'),'row_policy_invalid');return d.value;}
function checked(value,definition,schema,code){try{validate(value,schema.$defs[definition],schema,code);}catch(e){if(e instanceof StreamStagerRefusal)throw new CompilerRowFragmentRefusal(code);throw e;}}
function sum(...values){const result=values.reduce((a,b)=>a+b,0);need(values.every(n=>Number.isSafeInteger(n)&&n>=0)&&Number.isSafeInteger(result),'row_budget');return result;}
const denied=()=>Object.fromEntries(['filesystem_capture_attested','read_authority_granted','source_freshness_verified','table_membership_verified',
    'table_complete','actual_callsite_context_verified','helper_identity_resolved','binding_resolved','global_byte_line_mapping_verified',
    'actual_export_set_complete','input_code_evaluated','compiler_loaded','production_enabled','semantic_lineage','cache_eligible','publication_authority'].map(k=>[k,false]));
function observeSuppliedCompilerRowFragment(text,expectedInput,profilesInput,schemaInput,options){
    let context=null,raw=null,staged=null,result=null,stage='start',primary,failed=false;
    try{
        const now=options?.now||(()=>performance.now());need(typeof now==='function','row_deadline');
        const started=now();let last=started,deadline=options?.deadline;
        need(Number.isFinite(started),'row_deadline');
        const initialImage=field(profilesInput,'private_input_image_experiment'),initialLimit=field(field(initialImage,'limits'),'metadata_bytes');
        need(Number.isSafeInteger(initialLimit)&&initialLimit>0,'row_policy_invalid');
        stage='policy';context=copy({descriptor:field(profilesInput,PROFILE),windowLimits:field(field(profilesInput,'private_compiler_text_window'),'limits'),
            image:initialImage,schema:schemaInput,expected:expectedInput},'row_metadata_invalid',initialLimit);
        const {descriptor:d,windowLimits,image,schema,expected}=context;
        need(schema&&schema.$defs?.policy&&schema.$defs?.expected&&schema.$defs?.observation&&schema.$defs?.result,'row_policy_invalid');
        checked(d,'policy',schema,'row_policy_invalid');checked(expected,'expected',schema,'row_expected_invalid');
        need(d.version==='v1-private-supplied-local-row-fragment'&&d.model==='supplied_immutable_string_local_rows_v1'&&d.grammar==='literal_arrow_rows_ascii_v1'
            &&d.input_kind==='supplied_immutable_string_v1'&&d.text_policy==='strict_utf8_preserve_bom_shebang_crlf_v1'
            &&d.span_scheme==='view_local_utf16_half_open_strict_utf8_slice_v1'&&d.privacy==='local_row_fragment_summary_only_v1'
            &&d.complete_status==='LOCAL_ROW_FRAGMENT_OBSERVED_NOT_TABLE_OR_CALLSITE'&&d.incomplete_status==='INCOMPLETE_LOCAL_ROW_FRAGMENT_NOT_TABLE_OR_CALLSITE'
            &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>d[k]===false),'row_policy_invalid');
        need(d.schema_sha256===hash(schema)&&d.image_policy_sha256===hash(image)&&d.cursor_limit_reference_sha256===hash(windowLimits),'row_policy_invalid');
        const l=d.limits,r=d.resource_limits;
        need(Object.values(l).every(n=>Number.isSafeInteger(n)&&n>0)&&Object.values(r).every(n=>Number.isSafeInteger(n)&&n>0)
            &&Object.keys(l).every(k=>l[k]<=windowLimits[k])&&r.source_utf8_bytes<=4*l.scan_code_units
            &&r.metadata_bytes<=image.limits.metadata_bytes&&r.image_bytes<=image.limits.image_bytes&&r.operation_ms<=image.limits.capture_ms,'row_policy_invalid');
        if(deadline===undefined)deadline=started+r.operation_ms;
        need(Number.isFinite(deadline)&&started<deadline&&deadline<=started+r.operation_ms,'row_deadline');
        const check=()=>{const current=now();need(Number.isFinite(current)&&current>=last&&current<deadline,'row_deadline');last=current;};check();
        need(expected.policy_sha256===hash(d)&&expected.schema_sha256===hash(schema),'row_identity');
        stage='input';need(typeof text==='string','row_input_invalid');need(text.length<=l.scan_code_units,'row_budget');
        const metadata=sum(bytes(context),bytes(context));
        const reserved=sum(r.source_utf8_bytes,4*text.length,metadata,2*l.fact_bytes,2*l.name_utf8_bytes,l.gap_bytes,l.cursor_bytes,l.summary_bytes);
        need(metadata<=r.metadata_bytes&&reserved<=r.image_bytes,'row_budget');check();
        raw=Buffer.from(text,'utf8');need(raw.length<=r.source_utf8_bytes,'row_budget');
        need(raw.toString('utf8')===text,'row_encoding_invalid');
        need(expected.source_utf16_units===text.length&&expected.source_utf8_bytes===raw.length&&expected.source_text_sha256===sha(raw),'row_identity');
        const inputBytes=raw.length;raw=null;check();
        stage='scan';const metrics={scan_code_units:0,token_count:0,work_units:0,fact_bytes:0,name_utf8_bytes:0,gap_bytes:0};
        staged=recognizeRowFragment(text,l,check,metrics);checked(staged.observation,'observation',schema,'row_result_invalid');check();
        stage='settlement';const observation=staged.observation;staged.facts=null;staged=null;text=null;check();
        result={version:d.version,status:observation.fragment_syntax_complete?d.complete_status:d.incomplete_status,
            producer_policy_sha256:hash(d),producer_schema_sha256:hash(schema),expected_sha256:hash(expected),source_text_sha256:expected.source_text_sha256,
            observation,observation_sha256:hash(observation),...metrics,source_utf8_bytes:inputBytes,source_utf16_units:expected.source_utf16_units,
            source_identity_bytes_hashed:inputBytes,metadata_reserved_bytes:metadata,known_inputs_reserved_bytes:reserved,
            raw_source_retained:false,private_facts_cleared:true,absolute_mapping:null,flags:denied()};
        checked(result,'result',schema,'row_result_invalid');need(bytes(result)<=l.summary_bytes,'row_budget');
        stage='publish';check();Object.freeze(result.flags);if(observation.gap)Object.freeze(observation.gap);Object.freeze(observation);Object.freeze(result);check();
    }catch(error){primary=error instanceof CompilerExportRecipeRefusal?Object.assign(new CompilerRowFragmentRefusal(error.code==='recipe_budget'?'row_budget':'row_identity'),{cause:error}):error;failed=true;}
    finally{text=context=raw=staged=null;}
    if(failed){if(primary&&(['object','function'].includes(typeof primary))&&!stages.has(primary))stages.set(primary,stage);throw primary;}
    return result;
}
function failureStage(error){return error&&(['object','function'].includes(typeof error))?stages.get(error)||'unknown':'unknown';}
module.exports={observeSuppliedCompilerRowFragment,CompilerRowFragmentRefusal,failureStage};

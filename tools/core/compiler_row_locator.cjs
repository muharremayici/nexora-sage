'use strict';
// Disabled supplied-synthetic lexical candidates only. No acquisition, owner, evaluator or default caller.
const crypto=require('node:crypto'),{performance}=require('node:perf_hooks'),{types}=require('node:util');
const {locateRowCandidates,CompilerExportRecipeRefusal}=require('./compiler_source_recipe_cursor.cjs');
const {copy}=require('./compiler_text_window.cjs');
const {_canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate,StreamStagerRefusal}=require('./compiler_stream_stager.cjs');
const PROFILE='private_compiler_row_locator',stages=new WeakMap();
const sha=v=>crypto.createHash('sha256').update(v).digest('hex'),hash=v=>sha(canonical(v)),bytes=v=>Buffer.byteLength(canonical(v),'utf8');
class CompilerRowLocatorRefusal extends Error{constructor(code){super(code);this.code=code;}}
const need=(v,c)=>{if(!v)throw new CompilerRowLocatorRefusal(c);};
function field(v,k){need(v&&typeof v==='object'&&!types.isProxy(v),'locator_policy_invalid');const d=Object.getOwnPropertyDescriptor(v,k);need(d&&Object.hasOwn(d,'value'),'locator_policy_invalid');return d.value;}
function checked(v,d,s,c){try{validate(v,s.$defs[d],s,c);}catch(e){if(e instanceof StreamStagerRefusal)throw new CompilerRowLocatorRefusal(c);throw e;}}
function sum(...v){const n=v.reduce((a,b)=>a+b,0);need(v.every(x=>Number.isSafeInteger(x)&&x>=0)&&Number.isSafeInteger(n),'locator_budget');return n;}
const denied=()=>Object.fromEntries(['filesystem_capture_attested','read_authority_granted','source_freshness_verified','enclosing_lexical_context_verified',
    'table_membership_verified','table_complete','actual_callsite_context_verified','helper_identity_resolved','binding_resolved','global_byte_line_mapping_verified',
    'actual_export_set_complete','input_code_evaluated','compiler_loaded','production_enabled','semantic_lineage','cache_eligible','publication_authority'].map(k=>[k,false]));
function locateSuppliedCompilerRowCandidates(text,expectedInput,profilesInput,schemaInput,options){
    let context=null,raw=null,observation=null,result=null,stage='start',primary,failed=false;
    try{
        const now=options?.now||(()=>performance.now());need(typeof now==='function','locator_deadline');
        const started=now();let last=started,deadline=options?.deadline;need(Number.isFinite(started),'locator_deadline');
        const imageInput=field(profilesInput,'private_input_image_experiment'),ceiling=field(field(imageInput,'limits'),'metadata_bytes');
        need(Number.isSafeInteger(ceiling)&&ceiling>0,'locator_policy_invalid');
        stage='policy';context=copy({descriptor:field(profilesInput,PROFILE),row:field(profilesInput,'private_compiler_row_fragment'),
            image:imageInput,schema:schemaInput,expected:expectedInput},'locator_metadata_invalid',ceiling);
        const {descriptor:d,row,image,schema,expected}=context;
        need(schema?.$defs?.policy&&schema.$defs.expected&&schema.$defs.observation&&schema.$defs.gap&&schema.$defs.result,'locator_policy_invalid');
        checked(d,'policy',schema,'locator_policy_invalid');checked(expected,'expected',schema,'locator_expected_invalid');
        need(d.version==='v1-private-supplied-row-candidate-locator'&&d.model==='supplied_immutable_text_finite_row_occurrences_v1'
            &&d.grammar==='finite_ascii_single_arrow_occurrences_v1'&&d.input_kind==='supplied_immutable_string_v1'
            &&d.text_policy==='strict_utf8_preserve_bom_shebang_crlf_v1'&&d.span_scheme==='view_local_utf16_half_open_strict_utf8_slice_v1'
            &&d.privacy==='local_row_range_digest_only_v1'&&d.complete_status==='ROW_CANDIDATE_SCAN_COMPLETED_WITHIN_FINITE_MODEL_NOT_TABLE'
            &&d.incomplete_status==='PARTIAL_ROW_CANDIDATE_SCAN_UNKNOWN_LEXICAL_TAIL_NOT_TABLE'
            &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>d[k]===false),'locator_policy_invalid');
        need(row.version==='v1-private-supplied-local-row-fragment'&&row.grammar==='literal_arrow_rows_ascii_v1'
            &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>row[k]===false)
            &&d.schema_sha256===hash(schema)&&d.image_policy_sha256===hash(image)&&d.row_grammar_policy_sha256===hash(row)
            &&d.cursor_limit_reference_sha256===hash(row.limits),'locator_policy_invalid');
        const l=d.limits,r=d.resource_limits;
        need(Object.values(l).every(n=>Number.isSafeInteger(n)&&n>0)&&Object.values(r).every(n=>Number.isSafeInteger(n)&&n>0)
            &&Object.keys(l).every(k=>l[k]<=row.limits[k])&&Object.keys(r).every(k=>r[k]<=row.resource_limits[k])
            &&r.source_utf8_bytes<=4*l.scan_code_units&&r.metadata_bytes<=image.limits.metadata_bytes
            &&r.image_bytes<=image.limits.image_bytes&&r.operation_ms<=image.limits.capture_ms,'locator_policy_invalid');
        if(deadline===undefined)deadline=started+r.operation_ms;
        need(Number.isFinite(deadline)&&started<deadline&&deadline<=started+r.operation_ms,'locator_deadline');
        const check=()=>{const current=now();need(Number.isFinite(current)&&current>=last&&current<deadline,'locator_deadline');last=current;};check();
        need(expected.policy_sha256===hash(d)&&expected.schema_sha256===hash(schema),'locator_identity');
        stage='input';need(typeof text==='string','locator_input_invalid');need(text.length<=l.scan_code_units,'locator_budget');
        const metadata=10*bytes(context),p={u:l.scan_code_units,b:r.source_utf8_bytes,line:l.scan_code_units+1,column:l.scan_code_units};
        const tokenMetadata=8*bytes({kind:'string',value:'',start:p,end:p});
        const reserved=sum(2*r.source_utf8_bytes,4*text.length,metadata,4*l.cursor_bytes,2*tokenMetadata,5*l.fact_bytes,5*l.gap_bytes,5*l.summary_bytes);
        need(metadata<=r.metadata_bytes&&reserved<=r.image_bytes,'locator_budget');check();
        raw=Buffer.from(text,'utf8');need(raw.length<=r.source_utf8_bytes,'locator_budget');need(raw.toString('utf8')===text,'locator_encoding_invalid');
        need(raw.length===expected.source_utf8_bytes&&text.length===expected.source_utf16_units&&sha(raw)===expected.source_text_sha256,'locator_identity');
        const inputBytes=raw.length;raw=null;check();
        stage='scan';const metrics={scan_code_units:0,token_count:0,work_units:0,fact_bytes:0,name_utf8_bytes:0,gap_bytes:0,
            lookahead_peak_bytes:0,candidate_identity_bytes_hashed:0};
        observation=locateRowCandidates(text,l,check,metrics,d.row_grammar_policy_sha256);checked(observation,'observation',schema,'locator_result_invalid');
        if(observation.gap!==null)checked(observation.gap,'gap',schema,'locator_result_invalid');check();
        stage='settlement';text=null;check();
        result={version:d.version,status:observation.scan_complete_within_model?d.complete_status:d.incomplete_status,
            producer_policy_sha256:hash(d),producer_schema_sha256:hash(schema),expected_sha256:hash(expected),source_text_sha256:expected.source_text_sha256,
            observation,observation_sha256:hash(observation),...metrics,source_utf8_bytes:inputBytes,source_utf16_units:expected.source_utf16_units,
            source_identity_bytes_hashed:inputBytes,metadata_reserved_bytes:metadata,lookahead_metadata_reserved_bytes:tokenMetadata,known_inputs_reserved_bytes:reserved,
            raw_source_retained:false,private_facts_cleared:true,absolute_mapping:null,flags:denied()};
        checked(result,'result',schema,'locator_result_invalid');need(bytes(result)<=l.summary_bytes,'locator_budget');
        stage='publish';check();for(const candidate of observation.candidates)Object.freeze(candidate);
        Object.freeze(observation.candidates);if(observation.gap)Object.freeze(observation.gap);Object.freeze(observation);Object.freeze(result.flags);Object.freeze(result);check();
    }catch(error){primary=error instanceof CompilerExportRecipeRefusal?Object.assign(new CompilerRowLocatorRefusal(error.code==='recipe_budget'?'locator_budget':'locator_identity'),{cause:error}):error;failed=true;}
    finally{text=context=raw=observation=null;}
    if(failed){if(primary&&['object','function'].includes(typeof primary)&&!stages.has(primary))stages.set(primary,stage);throw primary;}
    return result;
}
function failureStage(e){return e&&['object','function'].includes(typeof e)?stages.get(e)||'unknown':'unknown';}
module.exports={locateSuppliedCompilerRowCandidates,CompilerRowLocatorRefusal,failureStage};

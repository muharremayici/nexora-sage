'use strict';
// Internal fixed text transaction. The public owner facade supplies private admission.
// This module is not a hostile same-process sandbox or caller authentication layer.
const path=require('node:path'),crypto=require('node:crypto'),{performance}=require('node:perf_hooks'),{types}=require('node:util');
const {recognize,CompilerExportRecipeRefusal}=require('./compiler_source_recipe_cursor.cjs');
const {_canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate,StreamStagerRefusal}=require('./compiler_stream_stager.cjs');
const PROFILE='private_compiler_text_window',OWNER='private_compiler_text_owner';
const sha=v=>crypto.createHash('sha256').update(v).digest('hex'),hash=v=>sha(canonical(v)),bytes=v=>Buffer.byteLength(canonical(v),'utf8');
const object=v=>v!==null&&(typeof v==='object'||typeof v==='function'),get=Object.getOwnPropertyDescriptor,has=Object.hasOwn;
const stages=new WeakMap();
class CompilerTextWindowRefusal extends Error{constructor(code){super(code);this.code=code;}}
const need=(v,code)=>{if(!v)throw new CompilerTextWindowRefusal(code);};
function note(e,s){if(object(e)&&!stages.has(e))stages.set(e,s);return e;}
function failureStage(e){return object(e)?stages.get(e)||'unknown':'unknown';}
function field(v,k,code='text_policy_invalid'){
    need(v&&typeof v==='object'&&!types.isProxy(v),code);const d=get(v,k);need(d&&has(d,'value'),code);return d.value;
}
function copy(v,code,ceiling,seen=new Set(),depth=0,budget={nodes:0,bytes:0}){
    need(depth<=32&&++budget.nodes<=ceiling,code);
    if(v===null||['string','boolean','number'].includes(typeof v)){
        budget.bytes+=typeof v==='string'?Buffer.byteLength(v,'utf8'):8;need(budget.bytes<=ceiling,code);return v;
    }
    need(v&&typeof v==='object'&&!types.isProxy(v)&&!seen.has(v),code);
    need(Array.isArray(v)||Object.getPrototypeOf(v)===Object.prototype||Object.getPrototypeOf(v)===null,code);seen.add(v);
    try{
        const ds=Object.getOwnPropertyDescriptors(v),ks=Reflect.ownKeys(ds);need(ks.length<=ceiling-budget.nodes&&ks.every(k=>typeof k==='string'&&has(ds[k],'value')),code);
        if(Array.isArray(v)){
            const n=ds.length?.value;need(Number.isSafeInteger(n)&&n>=0&&ks.length===n+1,code);
            return Array.from({length:n},(_,i)=>{need(has(ds,String(i)),code);return copy(ds[i].value,code,ceiling,seen,depth+1,budget);});
        }
        return Object.fromEntries(ks.map(k=>{budget.bytes+=Buffer.byteLength(k,'utf8');need(budget.bytes<=ceiling,code);return [k,copy(ds[k].value,code,ceiling,seen,depth+1,budget)];}));
    }finally{seen.delete(v);}
}
function checked(v,d,s,code){try{validate(v,d,s,code);}catch(e){if(e instanceof StreamStagerRefusal)throw new CompilerTextWindowRefusal(code);throw e;}}
function sum(...vs){const n=vs.reduce((a,b)=>a+b,0);need(vs.every(v=>Number.isSafeInteger(v)&&v>=0)&&Number.isSafeInteger(n),'text_budget');return n;}
function prepare(root,expectedInput,profilesInput,schemasInput){
    const imageInput=field(profilesInput,'private_input_image_experiment'),ceiling=field(field(imageInput,'limits'),'metadata_bytes');
    need(Number.isSafeInteger(ceiling)&&ceiling>0,'text_policy_invalid');
    const expected=copy(expectedInput,'text_expected_invalid',ceiling);
    need(expected&&has(expected,'selection')&&expected.selection!==null,'selection_not_available');
    const d=copy(field(profilesInput,PROFILE),'text_policy_invalid',ceiling),entry=copy(field(profilesInput,OWNER),'text_policy_invalid',ceiling);
    const capture=copy(field(profilesInput,'compiler_library_inputs'),'text_policy_invalid',ceiling),image=copy(imageInput,'text_policy_invalid',ceiling);
    const schemas=copy(schemasInput,'text_policy_invalid',ceiling),schema=schemas.text_window;
    need(Object.keys(schemas).sort().join(',')==='entry,text_window'&&schema?.$defs?.policy&&schemas.entry?.$defs?.policy,'text_policy_invalid');
    checked(d,schema.$defs.policy,schema,'text_policy_invalid');checked(entry,schemas.entry.$defs.policy,schemas.entry,'text_policy_invalid');
    checked(expected,schema.$defs.expected,schema,'text_expected_invalid');checked(expected.entry_expected,schemas.entry.$defs.expected,schemas.entry,'text_expected_invalid');
    need(d.version==='v1-private-declared-entry-text-window'&&d.model==='restricted_declared_entry_text_candidate_window_v1'
        &&d.entry_profile===OWNER&&entry.model==='single_declared_compiler_entry_text_only_v1'
        &&d.grammar==='literal_helper_site_ascii_v1'&&d.span_scheme==='view_local_utf16_half_open_strict_utf8_slice_v1'
        &&d.privacy==='declared_entry_text_window_summary_only_v1'&&d.consumer==='fixed_declared_text_window_metadata_consumer_v1'
        &&d.claim_scope==='exact_selected_declared_entry_text_only'&&d.callee==='__export'
        &&d.complete_status==='TEXT_WINDOW_CANDIDATE_OBSERVED_NOT_CALLSITE_ADMISSION'
        &&d.incomplete_status==='INCOMPLETE_TEXT_WINDOW_CANDIDATE_NOT_CALLSITE_ADMISSION'
        &&d.selection_policy.scheme==='utf16_half_open_exact_interval_v1'&&d.selection_policy.mapping==='checked_absolute_utf16_only_v1'
        &&d.selection_policy.context==='unverified_enclosing_source_v1'
        &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>d[k]===false&&entry[k]===false),'text_policy_invalid');
    need(d.schema_sha256===hash(schema)&&entry.schema_sha256===hash(schemas.entry)&&d.entry_policy_sha256===hash(entry)
        &&d.entry_schema_sha256===entry.schema_sha256&&d.capture_policy_sha256===hash(capture)&&d.image_policy_sha256===hash(image),'text_policy_invalid');
    const pins={observer_policy_sha256:hash(d),observer_schema_sha256:hash(schema),selection_schema_sha256:hash(schema.$defs.selection),
        selection_sha256:hash(expected.selection),authority_sha256:hash(expected.authority),selection_provenance_sha256:hash(expected.selection_provenance)};
    for(const k of ['grammar','span_scheme','privacy','limits','consumer','claim_scope','callee','whole_limits','selection_policy'])pins[k+'_sha256']=hash(d[k]);
    need(Object.entries(pins).every(([k,v])=>expected[k]===v),'text_identity');
    const e=expected.entry_expected,r=expected.entry,s=expected.selection,a=expected.authority,p=expected.selection_provenance;
    need(r.context_sha256===hash(e)&&['entry_sha256','text_sha256','policy_sha256','schema_sha256'].every(k=>r[k]===e[k])
        &&e.policy_sha256===hash(entry)&&e.schema_sha256===hash(schemas.entry)&&e.capture_policy_sha256===hash(capture)&&e.image_policy_sha256===hash(image),'text_identity');
    need(a.entry_context_sha256===r.context_sha256&&a.max_open_attempts===5&&a.max_entry_bytes>=e.entry_bytes
        &&a.max_entry_bytes<=capture.max_module_bytes&&a.max_total_bytes>=e.entry_bytes&&a.max_total_bytes<=capture.max_total_bytes,'text_authority_refused');
    if(root!==null){need(typeof root==='string'&&path.isAbsolute(root),'text_authority_refused');const normalized=path.resolve(root);
        need(a.root_sha256===sha(process.platform==='win32'?normalized.toLowerCase():normalized),'text_authority_refused');}
    need(p.entry_context_sha256===r.context_sha256&&p.method==='reviewed_exact_text_interval_v1'&&p.receipt_sha256!=='0'.repeat(64)
        &&s.provenance_sha256===hash(p)&&Object.keys(s).filter(k=>k!=='provenance_sha256').every(k=>s[k]===p[k]),'text_identity');
    need(s.start_utf16<s.end_utf16&&s.end_utf16<=s.source_code_units&&s.view_code_units===s.end_utf16-s.start_utf16
        &&s.view_utf8_bytes>0&&s.view_utf8_bytes<=4*s.view_code_units,'text_expected_invalid');
    const w=d.whole_limits,l=d.limits;
    need(Object.values(w).every(n=>Number.isSafeInteger(n)&&n>0)&&Object.values(l).every(n=>Number.isSafeInteger(n)&&n>0&&n<=ceiling)
        &&w.source_code_units<=capture.max_module_bytes&&w.verification_utf8_bytes<=capture.max_module_bytes
        &&w.verification_work_units<=sum(capture.max_module_bytes,capture.max_module_bytes)
        &&w.selection_work_units<=sum(capture.max_module_bytes,capture.max_module_bytes),'text_policy_invalid');
    const operation={expected,descriptor:d,entry,capture,image,schemas,pins};need(bytes(operation)<=ceiling,'text_budget');return operation;
}
module.exports={prepare,copy,checked,sum,need,note,CompilerTextWindowRefusal,CompilerExportRecipeRefusal,failureStage};

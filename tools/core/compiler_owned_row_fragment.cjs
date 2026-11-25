'use strict';
// Data-only admission/reservation. The owner module alone holds the private brand.
const crypto=require('node:crypto'),{types}=require('node:util');
const window=require('./compiler_text_window.cjs');
const {_canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate,StreamStagerRefusal}=require('./compiler_stream_stager.cjs');
const PROFILE='private_compiler_owned_row_fragment',stages=new WeakMap();
const hash=value=>crypto.createHash('sha256').update(canonical(value)).digest('hex'),bytes=value=>Buffer.byteLength(canonical(value),'utf8');
class CompilerOwnedRowFragmentRefusal extends Error{constructor(code){super(code);this.code=code;}}
const need=(value,code)=>{if(!value)throw new CompilerOwnedRowFragmentRefusal(code);};
function field(value,key){need(value&&typeof value==='object'&&!types.isProxy(value),'owned_row_policy');const d=Object.getOwnPropertyDescriptor(value,key);need(d&&Object.hasOwn(d,'value'),'owned_row_policy');return d.value;}
function checked(value,definition,schema,code){try{validate(value,schema.$defs[definition],schema,code);}catch(e){if(e instanceof StreamStagerRefusal)throw new CompilerOwnedRowFragmentRefusal(code);throw e;}}
function sum(...values){const n=values.reduce((a,b)=>a+b,0);need(values.every(x=>Number.isSafeInteger(x)&&x>=0)&&Number.isSafeInteger(n),'owned_row_budget');return n;}
function note(error,stage){if(error&&['object','function'].includes(typeof error)&&!stages.has(error))stages.set(error,stage);return error;}
function failureStage(error){return error&&['object','function'].includes(typeof error)?stages.get(error)||'unknown':'unknown';}
function prepare(root,expectedInput,profilesInput,schemasInput){
    const imageInput=field(profilesInput,'private_input_image_experiment'),ceiling=field(field(imageInput,'limits'),'metadata_bytes');
    need(Number.isSafeInteger(ceiling)&&ceiling>0,'owned_row_policy');
    const context=window.copy({expected:expectedInput,descriptor:field(profilesInput,PROFILE),schemas:schemasInput,
        profiles:Object.fromEntries(['private_compiler_text_owner','private_compiler_text_window','private_compiler_row_fragment','compiler_library_inputs','private_input_image_experiment'].map(k=>[k,field(profilesInput,k)]))},'owned_row_metadata',ceiling);
    const {expected:e,descriptor:d,schemas,profiles}=context,schema=schemas.owned_row_fragment,image=profiles.private_input_image_experiment;
    need(Object.keys(schemas).sort().join(',')==='entry,owned_row_fragment,row_fragment,text_window'&&schema?.$defs?.policy&&schema?.$defs?.expected,'owned_row_policy');
    checked(d,'policy',schema,'owned_row_policy');checked(e,'expected',schema,'owned_row_expected');
    need(d.version==='v1-private-owned-local-row-fragment'&&d.model==='declared_entry_exact_view_owned_local_rows_v1'
        &&d.consumer==='fixed_owned_local_row_metadata_consumer_v1'&&d.privacy==='owned_local_row_summary_only_v1'
        &&d.complete_status==='OWNED_LOCAL_ROW_FRAGMENT_OBSERVED_NOT_TABLE_OR_CALLSITE'
        &&d.incomplete_status==='INCOMPLETE_OWNED_LOCAL_ROW_FRAGMENT_NOT_TABLE_OR_CALLSITE'
        &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>d[k]===false),'owned_row_policy');
    const row=profiles.private_compiler_row_fragment,win=profiles.private_compiler_text_window;
    need(d.schema_sha256===hash(schema)&&d.entry_policy_sha256===hash(profiles.private_compiler_text_owner)
        &&d.window_policy_sha256===hash(win)&&d.fragment_policy_sha256===hash(row)&&d.fragment_schema_sha256===hash(schemas.row_fragment)
        &&d.image_policy_sha256===hash(image),'owned_row_policy');
    checked(row,'policy',schemas.row_fragment,'owned_row_policy');checked(e.fragment_expected,'expected',schemas.row_fragment,'owned_row_expected');
    need(row.version==='v1-private-supplied-local-row-fragment'&&row.model==='supplied_immutable_string_local_rows_v1'
        &&row.grammar==='literal_arrow_rows_ascii_v1'&&row.input_kind==='supplied_immutable_string_v1'
        &&row.text_policy==='strict_utf8_preserve_bom_shebang_crlf_v1'&&row.span_scheme==='view_local_utf16_half_open_strict_utf8_slice_v1'
        &&row.privacy==='local_row_fragment_summary_only_v1'&&row.complete_status==='LOCAL_ROW_FRAGMENT_OBSERVED_NOT_TABLE_OR_CALLSITE'
        &&row.incomplete_status==='INCOMPLETE_LOCAL_ROW_FRAGMENT_NOT_TABLE_OR_CALLSITE'
        &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>row[k]===false)
        &&row.schema_sha256===hash(schemas.row_fragment)&&row.image_policy_sha256===hash(image)&&row.cursor_limit_reference_sha256===hash(win.limits),'owned_row_policy');
    need(Object.values(row.limits).every(x=>Number.isSafeInteger(x)&&x>0)&&Object.values(row.resource_limits).every(x=>Number.isSafeInteger(x)&&x>0)
        &&Object.keys(row.limits).every(k=>row.limits[k]<=win.limits[k])&&row.resource_limits.source_utf8_bytes<=4*row.limits.scan_code_units
        &&row.resource_limits.metadata_bytes<=image.limits.metadata_bytes&&row.resource_limits.image_bytes<=image.limits.image_bytes
        &&row.resource_limits.operation_ms<=image.limits.capture_ms,'owned_row_policy');
    need(Object.values(d.limits).every(x=>Number.isSafeInteger(x)&&x>0)&&d.limits.metadata_bytes<=image.limits.metadata_bytes
        &&d.limits.image_bytes<=image.limits.image_bytes&&d.limits.operation_ms<=Math.min(image.limits.capture_ms,row.resource_limits.operation_ms)
        &&d.limits.summary_bytes<=win.limits.summary_bytes&&d.limits.capsule_bytes<=win.limits.summary_bytes,'owned_row_policy');
    const base=window.prepare(root,e.base_request,profiles,{entry:schemas.entry,text_window:schemas.text_window});
    const s=base.expected.selection,f=e.fragment_expected;
    const whole=base.descriptor.whole_limits;
    need(s.source_code_units<=whole.source_code_units&&base.expected.entry_expected.entry_bytes<=whole.verification_utf8_bytes
        &&sum(s.source_code_units,base.expected.entry_expected.entry_bytes)<=whole.verification_work_units
        &&sum(4,6*s.view_code_units)<=whole.selection_work_units,'owned_row_budget');
    need(e.integration_policy_sha256===hash(d)&&e.integration_schema_sha256===hash(schema)&&e.base_request_sha256===hash(e.base_request)
        &&e.fragment_expected_sha256===hash(f)&&e.invocation_id===base.expected.entry_expected.invocation_id&&f.invocation_id===e.invocation_id
        &&e.purpose==='observe_owned_local_row_fragment'&&e.integration_authority_ref_sha256!=='0'.repeat(64)
        &&f.policy_sha256===hash(row)&&f.schema_sha256===hash(schemas.row_fragment)&&f.source_text_sha256===s.view_text_sha256
        &&f.source_utf16_units===s.view_code_units&&f.source_utf8_bytes===s.view_utf8_bytes,'owned_row_identity');
    need(s.view_code_units<=row.limits.scan_code_units&&s.view_utf8_bytes<=row.resource_limits.source_utf8_bytes,'owned_row_budget');
    const fragmentContext={descriptor:row,windowLimits:win.limits,image,schema:schemas.row_fragment,expected:f};
    const l=row.limits,r=row.resource_limits,fragmentMetadata=sum(bytes(fragmentContext),bytes(fragmentContext));
    const fragmentReserved=sum(r.source_utf8_bytes,4*s.view_code_units,fragmentMetadata,2*l.fact_bytes,2*l.name_utf8_bytes,l.gap_bytes,l.cursor_bytes,l.summary_bytes);
    need(fragmentMetadata<=r.metadata_bytes&&fragmentReserved<=r.image_bytes,'owned_row_budget');
    const op={expected:e,descriptor:d,base,fragmentContext,schemas,fragment_reserved_bytes:fragmentReserved};
    const metadata=sum(bytes(op),bytes(op));need(metadata<=d.limits.metadata_bytes,'owned_row_budget');
    const lower=sum(base.expected.entry_expected.entry_bytes,2*s.source_code_units,base.expected.entry_expected.entry_bytes,metadata,2*s.view_code_units,
        fragmentReserved,2*d.limits.capsule_bytes,d.limits.summary_bytes);
    need(lower<=d.limits.image_bytes,'owned_row_budget');return op;
}
function reserve(op,snapshot){
    const s=op.base.expected.selection,d=op.descriptor;
    const metadata=sum(bytes(op),bytes(op),bytes(snapshot));need(metadata<=d.limits.metadata_bytes,'owned_row_budget');
    const total=sum(snapshot.known_owner_input_bytes,2*s.source_code_units,op.base.expected.entry_expected.entry_bytes,metadata,
        2*s.view_code_units,op.fragment_reserved_bytes,2*d.limits.capsule_bytes,d.limits.summary_bytes);
    need(total<=d.limits.image_bytes,'owned_row_budget');return {observer_metadata_bytes:metadata,fragment_reserved_bytes:op.fragment_reserved_bytes,known_inputs_reserved_bytes:total};
}
module.exports={prepare,reserve,checked,sum,need,note,failureStage,CompilerOwnedRowFragmentRefusal};

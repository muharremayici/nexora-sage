'use strict';
// Private supplied-synthetic content evidence only; no file, locator, owner or default caller.
const crypto=require('node:crypto'),{performance}=require('node:perf_hooks'),{types}=require('node:util');
const {copy,CompilerTextWindowRefusal}=require('./compiler_text_window.cjs');
const {_canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate,StreamStagerRefusal}=require('./compiler_stream_stager.cjs');
const PROFILE='private_compiler_span_selection',stages=new WeakMap(),failures=new WeakMap();
const sha=v=>crypto.createHash('sha256').update(v).digest('hex'),hash=v=>sha(canonical(v)),bytes=v=>Buffer.byteLength(canonical(v),'utf8');
class CompilerSpanSelectionRefusal extends Error{constructor(code){super(code);this.code=code;}}
const need=(v,c)=>{if(!v)throw new CompilerSpanSelectionRefusal(c);};
function field(v,k){need(v&&typeof v==='object'&&!types.isProxy(v),'span_policy_invalid');const d=Object.getOwnPropertyDescriptor(v,k);need(d&&Object.hasOwn(d,'value'),'span_policy_invalid');return d.value;}
function checked(v,k,s,c){try{validate(v,s.$defs[k],s,c);}catch(e){if(e instanceof StreamStagerRefusal)throw new CompilerSpanSelectionRefusal(c);throw e;}}
function supported(s){
    const allowed=new Set(['$schema','$id','title','$defs','$ref','type','required','properties','additionalProperties','const','enum','items','minItems','minLength','minimum','pattern']);
    need(s&&typeof s==='object'&&!Array.isArray(s)&&Object.keys(s).every(k=>allowed.has(k)),'span_policy_invalid');
    for(const map of [s.$defs,s.properties])if(map)for(const child of Object.values(map))supported(child);
    if(s.items)supported(s.items);
}
function sum(...values){const n=values.reduce((a,b)=>a+b,0);need(values.every(v=>Number.isSafeInteger(v)&&v>=0)&&Number.isSafeInteger(n),'span_budget');return n;}
function denied(){return Object.fromEntries(['filesystem_capture_attested','read_authority_granted','source_freshness_verified','same_read_attested',
    'origin_authenticated','selection_decision_authenticated','controller_request_admitted','table_membership_verified','binding_resolved',
    'actual_export_set_complete','input_code_evaluated','compiler_loaded','production_enabled','semantic_lineage','cache_eligible','publication_authority'].map(k=>[k,false]));}
function atomic(text,u){
    if(u===0||u===text.length)return true;
    const left=text.charCodeAt(u-1),right=text.charCodeAt(u);
    return !(left>=0xD800&&left<=0xDBFF&&right>=0xDC00&&right<=0xDFFF)&&!(left===13&&right===10);
}
function produceSuppliedExactSpanSelection(text,expectedInput,profilesInput,schemaInput,options){
    let context=null,raw=null,view=null,receipt=null,wire=null,output=null,stage='start',primary,failed=false;
    const incurred={source_identity_bytes_hashed:0,mapping_code_units_visited:0,mapping_work_units:0,view_bytes_hashed:0};
    try{
        const now=options?.now||(()=>performance.now());need(typeof now==='function','span_deadline');
        const started=now();let last=started,deadline=options?.deadline;need(Number.isFinite(started),'span_deadline');
        const imageInput=field(profilesInput,'private_input_image_experiment'),ceiling=field(field(imageInput,'limits'),'metadata_bytes');
        need(Number.isSafeInteger(ceiling)&&ceiling>0,'span_policy_invalid');
        stage='policy';context=copy({descriptor:field(profilesInput,PROFILE),row:field(profilesInput,'private_compiler_row_fragment'),
            image:imageInput,schema:schemaInput,expected:expectedInput},'span_metadata_invalid',ceiling);
        const {descriptor:d,row,image,schema,expected}=context,l=d.limits;
        need(l&&Object.values(l).every(v=>Number.isSafeInteger(v)&&v>0),'span_policy_invalid');
        const metadata=sum(10*bytes(context));need(metadata<=l.metadata_bytes,'span_budget');
        supported(schema);need(['policy','expected','content','cost','flags','receipt'].every(k=>schema.$defs?.[k]),'span_policy_invalid');
        checked(d,'policy',schema,'span_policy_invalid');checked(expected,'expected',schema,'span_expected_invalid');
        need(d.version==='v1-private-supplied-exact-span-selection'&&d.model==='supplied_immutable_text_exact_utf16_span_v1'
            &&d.method==='reviewed_exact_utf16_span_v1'&&d.purpose==='record_supplied_exact_span'&&expected.purpose===d.purpose
            &&d.input_kind==='supplied_immutable_string_v1'&&d.text_policy==='strict_utf8_preserve_bom_shebang_crlf_v1'
            &&d.boundary_policy==='codepoint_and_crlf_atomic_v1'&&d.span_scheme==='supplied_text_local_utf16_utf8_half_open_v1'
            &&d.privacy==='exact_span_content_metadata_only_v1'&&d.receipt_status==='EXACT_SPAN_CONTENT_SELECTION_RECORDED_NOT_FILE_ATTESTATION'
            &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>d[k]===false),'span_policy_invalid');
        need(row.version==='v1-private-supplied-local-row-fragment'&&row.grammar==='literal_arrow_rows_ascii_v1'
            &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>row[k]===false)
            &&d.schema_sha256===hash(schema)&&d.reference_policy_sha256===hash(row)&&d.image_policy_sha256===hash(image),'span_policy_invalid');
        const caps={source_utf16_units:row.limits.scan_code_units,source_utf8_bytes:row.resource_limits.source_utf8_bytes,
            view_utf16_units:row.limits.scan_code_units,view_utf8_bytes:row.resource_limits.source_utf8_bytes,
            metadata_bytes:row.resource_limits.metadata_bytes,receipt_bytes:row.limits.summary_bytes,cursor_bytes:row.limits.cursor_bytes,
            mapping_work_units:row.limits.work_units,image_bytes:row.resource_limits.image_bytes,operation_ms:row.resource_limits.operation_ms};
        need(Object.keys(l).sort().join(',')===Object.keys(caps).sort().join(',')&&Object.keys(caps).every(k=>Number.isSafeInteger(caps[k])&&caps[k]>0&&l[k]<=caps[k])
            &&l.metadata_bytes<=image.limits.metadata_bytes&&l.image_bytes<=image.limits.image_bytes&&l.operation_ms<=image.limits.capture_ms,'span_policy_invalid');
        if(deadline===undefined)deadline=started+l.operation_ms;
        need(Number.isFinite(deadline)&&started<deadline&&deadline<=started+l.operation_ms,'span_deadline');
        const check=()=>{const current=now();need(Number.isFinite(current)&&current>=last&&current<deadline,'span_deadline');last=current;};check();
        need(expected.policy_sha256===hash(d)&&expected.schema_sha256===hash(schema),'span_identity');
        stage='input';need(typeof text==='string','span_input_invalid');need(text.length<=l.source_utf16_units,'span_budget');
        const start=expected.start_utf16,end=expected.end_utf16;
        need([start,end,expected.source_utf16_units,expected.source_utf8_bytes].every(Number.isSafeInteger)
            &&start>=0&&start<end&&end<=text.length,'span_range_invalid');
        need(end-start<=l.view_utf16_units,'span_budget');need(atomic(text,start)&&atomic(text,end),'span_boundary_invalid');
        const scratch=bytes({unit:l.source_utf16_units,byte:l.source_utf8_bytes,start_utf8:l.source_utf8_bytes,end_utf8:l.source_utf8_bytes,check_at:l.source_utf16_units});
        const reserved=sum(2*l.source_utf8_bytes,4*text.length,metadata,2*l.cursor_bytes,10*l.receipt_bytes);
        need(scratch<=l.cursor_bytes&&reserved<=l.image_bytes,'span_budget');check();
        raw=Buffer.from(text,'utf8');need(raw.length<=l.source_utf8_bytes,'span_budget');need(raw.toString('utf8')===text,'span_encoding_invalid');
        const sourceHash=sha(raw);incurred.source_identity_bytes_hashed=raw.length;
        need(raw.length===expected.source_utf8_bytes&&text.length===expected.source_utf16_units&&sourceHash===expected.source_text_utf8_sha256,'span_identity');check();
        stage='mapping';let unit=0,byte=0,startByte=start===0?0:null,work=0;
        while(unit<end){
            check();const point=text.codePointAt(unit),width=point>0xFFFF?2:1,encoded=point<0x80?1:point<0x800?2:point<0x10000?3:4;
            work=sum(work,width,encoded);incurred.mapping_work_units=work;need(work<=l.mapping_work_units,'span_budget');unit+=width;byte+=encoded;
            incurred.mapping_code_units_visited=unit;
            if(unit===start)startByte=byte;
        }
        need(unit===end&&startByte!==null&&startByte<byte&&byte<=raw.length,'span_range_invalid');
        const viewBytes=byte-startByte;need(viewBytes<=l.view_utf8_bytes,'span_budget');
        view=raw.subarray(startByte,byte);const viewHash=sha(view);incurred.view_bytes_hashed=viewBytes;check();
        const content={source_text_utf8_sha256:expected.source_text_utf8_sha256,source_utf16_units:text.length,source_utf8_bytes:raw.length,
            method:d.method,boundary_policy:d.boundary_policy,span_scheme:d.span_scheme,start_utf16:start,end_utf16:end,start_utf8:startByte,end_utf8:byte,
            view_utf16_units:end-start,view_utf8_bytes:viewBytes,view_text_sha256:viewHash};
        const cost={source_identity_bytes_hashed:raw.length,mapping_code_units_visited:unit,mapping_work_units:work,view_bytes_hashed:viewBytes,
            metadata_reserved_bytes:metadata,source_buffer_reserved_bytes:2*l.source_utf8_bytes,source_text_reserved_bytes:4*text.length,
            cursor_reserved_bytes:2*l.cursor_bytes,receipt_reserved_bytes:10*l.receipt_bytes,mapping_cursor_metadata_bytes:scratch,known_inputs_reserved_bytes:reserved};
        // The selected view aliases raw; neither source representation crosses the receipt boundary.
        text=raw=view=null;stage='settlement';check();
        receipt={version:d.version,status:d.receipt_status,purpose:d.purpose,producer_policy_sha256:hash(d),producer_schema_sha256:hash(schema),
            expected_sha256:hash(expected),content,content_sha256:hash(content),origin_ref_sha256:expected.origin_ref_sha256,selection_ref_sha256:expected.selection_ref_sha256,
            cost,raw_source_retained:false,private_refs_cleared:true,flags:denied()};
        checked(receipt,'receipt',schema,'span_receipt_invalid');check();
        stage='publish';Object.freeze(content);Object.freeze(cost);Object.freeze(receipt.flags);Object.freeze(receipt);check();
        wire=canonical(receipt);need(Buffer.byteLength(wire,'utf8')<=l.receipt_bytes,'span_budget');
        output=Buffer.from(wire,'utf8');check();
    }catch(error){primary=error instanceof CompilerTextWindowRefusal?Object.assign(new CompilerSpanSelectionRefusal(error.code),{cause:error}):error;failed=true;}
    finally{text=context=raw=view=receipt=wire=null;if(failed)output=null;}
    if(failed){
        if(primary&&['object','function'].includes(typeof primary)){
            if(!stages.has(primary))stages.set(primary,stage);
            failures.set(primary,{stage,receipt_emitted:false,...incurred});
        }
        throw primary;
    }
    return output;
}
function failureStage(error){return error&&['object','function'].includes(typeof error)?stages.get(error)||'unknown':'unknown';}
function failureObservation(error){const observation=error&&['object','function'].includes(typeof error)?failures.get(error):null;return observation?{...observation}:null;}
module.exports={produceSuppliedExactSpanSelection,CompilerSpanSelectionRefusal,failureStage,failureObservation};

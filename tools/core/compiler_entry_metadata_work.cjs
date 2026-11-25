'use strict';
// Invocation-local modeled direct metadata work only; no I/O, clock or producer.
const crypto=require('node:crypto'),{types}=require('node:util');
const {_canonicalCompilerStreamMetadata:canonical}=require('./compiler_stream_stager.cjs');
const VERSION='v1-private-direct-context-metadata-work';
const SCOPE='direct_handoff_canonical_sha256_passes_only';
const UNIT='modeled_utf8_byte_work';
const EXCLUDED=['shared_copy','shared_validation','producer_internal','python_controller','transport','native_physical'];
class EntryContextMetadataWorkRefusal extends Error{constructor(code){super(code);this.code=code;}}
const need=(ok,code)=>{if(!ok)throw new EntryContextMetadataWorkRefusal(code);};
const nat=n=>Number.isSafeInteger(n)&&n>=0;
function add(a,b){const n=a+b;need(nat(a)&&nat(b)&&nat(n),'metadata_work_arithmetic');return n;}
function _createEntryContextMetadataWorkMeter(input,outputBytes){
    need(input&&typeof input==='object'&&!types.isProxy(input)&&!Array.isArray(input),'metadata_work_policy');
    const ds=Object.getOwnPropertyDescriptors(input),names=Reflect.ownKeys(ds);
    const expected=['version','scope','unit','canonical_units_per_byte','hash_units_per_byte','limit_units',
        'terminal_forms','terminal_failure_bytes','state_bytes','snapshot_bytes','excluded_domains'];
    need(names.every(k=>typeof k==='string'&&Object.hasOwn(ds[k],'value'))&&names.length===expected.length
        &&expected.every(k=>Object.hasOwn(ds,k)),'metadata_work_policy');
    const d=Object.fromEntries(names.map(k=>[k,ds[k].value]));
    need(d.version===VERSION&&d.scope===SCOPE&&d.unit===UNIT&&d.canonical_units_per_byte===2&&d.hash_units_per_byte===1
        &&d.terminal_forms===2&&d.state_bytes===4096&&d.snapshot_bytes===4096
        &&Array.isArray(d.excluded_domains)&&!types.isProxy(d.excluded_domains)
        &&d.excluded_domains.length===EXCLUDED.length&&d.excluded_domains.every((v,i)=>v===EXCLUDED[i]),'metadata_work_policy');
    need(nat(d.limit_units)&&d.limit_units>0&&d.limit_units<=2097152&&nat(outputBytes)&&outputBytes>0&&outputBytes<=65536
        &&nat(d.terminal_failure_bytes)&&d.terminal_failure_bytes>0&&d.terminal_failure_bytes<=65536,'metadata_work_policy');
    const terminal=d.canonical_units_per_byte*(outputBytes+d.terminal_failure_bytes);
    need(nat(terminal)&&terminal<=d.limit_units,'metadata_work_budget');
    let canonicalAttempts=0,canonicalReturns=0,canonicalUnknown=0,canonicalBytes=0;
    let hashRequests=0,hashAttempts=0,hashReturns=0,hashUnknown=0,hashRequestedBytes=0,hashAdmittedBytes=0,hashReturnedBytes=0;
    let modeled=0,attempted=0,exhausted=false,unknown=false,sealed=false,preProducer=null;
    const before=()=>{need(!sealed,'metadata_work_closed');need(!unknown,'metadata_work_incomplete');need(!exhausted,'metadata_work_budget');};
    const budget=()=>{if(add(attempted,terminal)>d.limit_units){exhausted=true;throw new EntryContextMetadataWorkRefusal('metadata_work_budget');}};
    function canonicalRecord(value){
        before();canonicalAttempts=add(canonicalAttempts,1);
        let text,n;
        try{text=canonical(value);n=Buffer.byteLength(text,'utf8');}
        catch(error){canonicalUnknown=add(canonicalUnknown,1);unknown=true;throw error;}
        canonicalReturns=add(canonicalReturns,1);canonicalBytes=add(canonicalBytes,n);
        const units=d.canonical_units_per_byte*n;
        modeled=add(modeled,units);attempted=add(attempted,units);
        // Length is known from this one completed pass. Reject before later effects;
        // a completed but over-budget pass is retained, never refunded.
        budget();return {text,n};
    }
    function hash(value){
        before();hashRequests=add(hashRequests,1);
        const {text,n}=canonicalRecord(value);
        hashRequestedBytes=add(hashRequestedBytes,n);attempted=add(attempted,d.hash_units_per_byte*n);
        budget();
        hashAdmittedBytes=add(hashAdmittedBytes,n);modeled=add(modeled,d.hash_units_per_byte*n);
        hashAttempts=add(hashAttempts,1);
        try{
            const result=crypto.createHash('sha256').update(text,'utf8').digest('hex');
            hashReturns=add(hashReturns,1);hashReturnedBytes=add(hashReturnedBytes,n);return result;
        }catch(error){hashUnknown=add(hashUnknown,1);unknown=true;throw error;}
    }
    function snapshot(){
        return Object.freeze({version:VERSION,scope:SCOPE,unit:UNIT,
            canonical_units_per_byte:d.canonical_units_per_byte,hash_units_per_byte:d.hash_units_per_byte,
            canonical_attempts:canonicalAttempts,canonical_returns:canonicalReturns,canonical_unknown_attempts:canonicalUnknown,
            canonical_returned_utf8_bytes:canonicalBytes,hash_requests:hashRequests,hash_attempts:hashAttempts,
            hash_returns:hashReturns,hash_unknown_attempts:hashUnknown,hash_input_requested_utf8_bytes:hashRequestedBytes,
            hash_input_admitted_utf8_bytes:hashAdmittedBytes,hash_input_returned_utf8_bytes:hashReturnedBytes,
            modeled_work_units:modeled,attempted_work_units:attempted,limit_units:d.limit_units,
            terminal_reserved_units:terminal,terminal_publication_bytes:outputBytes,terminal_failure_bytes:d.terminal_failure_bytes,
            pre_producer_units:preProducer,budget_exhausted:exhausted,
            unknown_direct_work:unknown,measurement_sealed:sealed,terminal_work_measured:false,
            measured_through:'direct_body_before_terminal_publication',coverage_complete:false,complete_cost_priced:false,
            full_work_units:null,physical_fit:null,excluded_domains:Object.freeze([...EXCLUDED])});
    }
    return Object.freeze({
        canonical(value){return canonicalRecord(value).text;},
        bytes(value){return canonicalRecord(value).n;},
        hash,snapshot,
        markProducerEntry(){before();need(preProducer===null,'metadata_work_producer_marker');preProducer=modeled;},
        seal(){sealed=true;return snapshot();}
    });
}
module.exports={_createEntryContextMetadataWorkMeter,EntryContextMetadataWorkRefusal};

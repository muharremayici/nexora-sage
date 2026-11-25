'use strict';
// Explicit private first-party-root operation. No default caller or entry evaluator.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {performance}=require('node:perf_hooks'),{types}=require('node:util');
const {_canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate,StreamStagerRefusal}=require('./compiler_stream_stager.cjs');
const PROFILE='private_compiler_entry_owner',owners=new WeakMap(),textOwners=new WeakMap(),rowOwners=new WeakMap();
const textWindow=require('./compiler_text_window.cjs');
const ownedRow=require('./compiler_owned_row_fragment.cjs');
const {observeSuppliedCompilerRowFragment}=require('./compiler_row_fragment.cjs');
const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
const hash=value=>sha(canonical(value));
const bytes=value=>Buffer.byteLength(canonical(value),'utf8');
const flags=()=>Object.fromEntries(['entry_evaluated','compiler_loaded','loaded_code_attested','native_execution_attested','source_freshness_verified',
    'snapshot_bound','image_admitted','production_enabled','semantic_lineage','cache_eligible'].map(k=>[k,false]));
class CompilerEntryRefusal extends Error {constructor(code){super(code);this.code=code;}}
const refuse=code=>{throw new CompilerEntryRefusal(code);};
const need=(value,code)=>{if(!value)refuse(code);};
const samePath=(a,b)=>process.platform==='win32'?path.normalize(a).toLowerCase()===path.normalize(b).toLowerCase():a===b;
const unavailable=new Set(['ENOENT','ENOTDIR','EACCES','EPERM','ELOOP','EISDIR']);
function fileOperation(callback){
    try{return callback();}
    catch(error){if(unavailable.has(error.code))refuse('entry_input_unavailable');throw error;}
}

function dataCopy(value,code){
    need(value&&typeof value==='object'&&!Array.isArray(value),code);
    const props=Object.getOwnPropertyDescriptors(value);
    need(Reflect.ownKeys(props).every(k=>typeof k==='string'&&Object.hasOwn(props[k],'value')),code);
    return Object.fromEntries(Object.keys(props).map(k=>[k,props[k].value]));
}

function makeOwner(record,refs,metrics,descriptor,check,registry=owners,operation=null,trace=null){
    let held=record,state='READY',code='',privateRow=null;record=null;
    const forget=()=>{held=null;if(privateRow)privateRow.operation=null;registry.delete(owner);};
    const owner={
        snapshot(){return {version:descriptor.version,status:descriptor.snapshot_status,state,code,...refs,...metrics,
            retained_entry_bytes:held===null?0:held.size_bytes,retained_text_code_units:held===null?0:held.text.length,
            open_file_handles:0,entry_input_verified:held!==null,entry_owner_registered:registry.has(owner),
            whole_image_within_limit:null,peak_rss_bytes:null,...flags()};},
        dispose(){forget();state='DISPOSED';if(trace)trace.released(state);},
        toJSON(){throw new TypeError('compiler_entry_owner_not_serializable');},
        [Symbol.for('nodejs.util.inspect.custom')](){return '<private compiler entry '+state+'>';}
    };
    let published=false;
    try{
        privateRow={dispose:owner.dispose,operation,check,borrow(expectedInput,callback){
            let settled=false;
            try{
                check();need(state==='READY','entry_order');state='BORROWING';
                need(typeof callback==='function'&&!types.isAsyncFunction(callback)&&!types.isGeneratorFunction(callback),'entry_order');
                const expected=dataCopy(expectedInput,'entry_expected_invalid');
                const keys=Object.keys(refs);
                need(Object.keys(expected).length===keys.length&&keys.every(k=>typeof expected[k]==='string'&&/^[a-f0-9]{64}$/.test(expected[k])),'entry_expected_invalid');
                need(keys.every(k=>expected[k]===refs[k]),'entry_identity');
                // Metadata proxy traps can dispose/reenter this owner; poison the outer borrow too.
                need(state==='BORROWING'&&held!==null,'entry_order');check();
                if(trace)trace.note('borrow_begin','entry',held.size_bytes,held.text.length);
                const value=callback(Object.freeze({text:held.text,raw_sha256:held.raw_sha256,text_sha256:held.text_sha256,size_bytes:held.size_bytes}));
                if(value&&typeof value.then==='function')refuse('entry_order');
                need(state==='BORROWING'&&held!==null,'entry_order');check();settled=true;return value;
            }catch(error){code=error instanceof CompilerEntryRefusal?error.code:'entry_internal_error';throw error;}
            finally{forget();state=settled?'DISPOSED':'ABORTED';if(trace)trace.released(state);}
        }};registry.set(owner,privateRow);operation=null;
        const result=Object.freeze(owner);check();need(state==='READY'&&held!==null&&registry.has(owner),'entry_order');published=true;return result;
    }finally{if(!published){forget();state='ABORTED';if(trace)trace.released(state);}}
}

function _borrowCompilerEntryText(owner,expected,callback){
    const entry=owners.get(owner);if(!entry)refuse('entry_order');
    return entry.borrow(expected,callback);
}
function _disposeCompilerEntry(owner){
    const entry=owners.get(owner);if(!entry)return false;
    entry.dispose();return true;
}

function createCheckedReadSession(root,d,capture,limit,inputBytes,check,textOnly,operation,manifestOperation,expected,sidecar=null,trace=null){
    let total=0,metadataBytes=inputBytes,openAttempts=0;const records=[];
    const guard=()=>{check();need(metadataBytes<=limit.metadata_bytes&&total<=capture.max_total_bytes&&total+metadataBytes<=limit.image_bytes,'entry_budget');};
    const relative=value=>{
        need(typeof value==='string'&&new RegExp(d.relative_path_pattern,'u').test(value)&&!path.posix.isAbsolute(value)
            &&!value.split('/').includes('..'),'entry_path_refused');
        const normalized=path.posix.normalize(value);need(normalized!=='.','entry_path_refused');return normalized;
    };
    function readOnce(base,member,role,module=false){
        guard();member=relative(member);
        const candidatePath=path.join(base,...member.split('/'));
        if(trace)trace.path(role,candidatePath);
        if(sidecar)sidecar.beforePath(role,candidatePath);
        need(samePath(fileOperation(()=>fs.realpathSync(candidatePath)),candidatePath),'entry_path_refused');
        const cap=Math.min(module?capture.max_module_bytes:capture.max_file_bytes,capture.max_total_bytes-total,
            module?limit.image_bytes-total-metadataBytes:limit.metadata_bytes-metadataBytes,
            textOnly?operation.expected.authority.max_total_bytes-total:Number.MAX_SAFE_INTEGER,
            textOnly&&module?operation.expected.authority.max_entry_bytes:Number.MAX_SAFE_INTEGER);
        need(Number.isSafeInteger(cap)&&cap>=0,'entry_budget');
        if(textOnly)need(++openAttempts<=operation.expected.authority.max_open_attempts,'entry_budget');
        if(sidecar)sidecar.beforeOpen(role,member);
        let fd;
        if(trace)trace.io('open_attempt',role);
        try{fd=fileOperation(()=>fs.openSync(candidatePath,'r'));if(sidecar)sidecar.openReturned();if(trace)trace.io('open_return',role);}
        catch(error){if(sidecar)sidecar.openUnknown();if(trace)trace.io('open_unknown',role);throw error;}
        let buffer=null,failed=false;
        try{
            const stat=fileOperation(()=>fs.fstatSync(fd));
            need(stat.isFile(),'entry_path_refused');need(Number.isSafeInteger(stat.size)&&stat.size>=0&&stat.size<=cap,'entry_budget');
            if(trace)trace.stat(role,stat.size);
            if(sidecar)sidecar.stat(role,stat.size);
            if(trace&&module)trace.note('allocation_request',role,stat.size+1,stat.size);
            buffer=Buffer.alloc(stat.size+1);let count=0;
            if(trace&&module)trace.note('backing_ready',role,buffer.length,stat.size);
            while(count<buffer.length){
                check();if(sidecar)sidecar.readAttempt();let n;
                if(trace&&module)trace.note('read_attempt',role,null,null);
                try{n=fileOperation(()=>fs.readSync(fd,buffer,count,buffer.length-count,null));}
                catch(error){if(sidecar)sidecar.readUnknown();if(trace&&module)trace.safe('read_unknown',role);throw error;}
                if(trace&&module)trace.note('read_return',role,Number.isSafeInteger(n)&&n>=0?n:null,null);
                if(sidecar)sidecar.readReturned(role,n,buffer.length-count);
                need(Number.isSafeInteger(n)&&n>=0&&n<=buffer.length-count,'entry_input_invalid');
                if(n===0)break;count+=n;
            }
            need(count===stat.size&&fileOperation(()=>fs.fstatSync(fd)).size===stat.size,'entry_identity');
            const value=buffer.subarray(0,count);buffer=null;total+=count;if(!module)metadataBytes+=count;
            records.push({role,size_bytes:count,raw_sha256:sha(value),relative_path_sha256:sha(member)});
            if(sidecar)sidecar.record(records[records.length-1]);
            metadataBytes+=bytes(records[records.length-1]);guard();return value;
        }catch(error){failed=true;if(sidecar?.readFailed)sidecar.readFailed(error);throw error;}
        finally{
            buffer=null;if(sidecar)sidecar.closeAttempt();if(trace)trace.io('close_attempt',role);
            try{fileOperation(()=>fs.closeSync(fd));if(sidecar)sidecar.closeReturned();if(trace)trace.io('close_return',role);}
            catch(error){
                if(trace)trace.io('close_unknown',role);
                if(sidecar){sidecar.closeUnknown(error,failed);if(!failed)throw error;}
                else if(!trace||!failed)throw error;
            }
        }
    }
    function decode(value){
        try{return new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(value);}
        catch(error){if(error instanceof TypeError)refuse('entry_encoding_refused');throw error;}
    }
    function metadata(member,role,expectedHash,base=root){
        const value=readOnce(base,member,role);need(sha(value)===expectedHash,'entry_identity');
        const decoded=decode(value).replace(/^\ufeff/,'');let parsed;
        try{parsed=JSON.parse(decoded,(key,item)=>{
            check();
            need(Buffer.from(key,'utf8').toString('utf8')===key,'entry_input_invalid');
            if(typeof item==='string')need(Buffer.from(item,'utf8').toString('utf8')===item,'entry_input_invalid');
            if(typeof item==='number')need(Number.isFinite(item),'entry_input_invalid');
            return item;
        });}catch(error){if(error instanceof SyntaxError)refuse('entry_input_invalid');throw error;}
        // Native JSON owns grammar; this bounded token pass only checks duplicate decoded keys.
        const stack=[];
        for(const token of decoded.matchAll(/"(?:[^"\\]|\\[\s\S])*"|[{}\[\]:,]/g)){
            check();const part=token[0],top=stack[stack.length-1];
            if(part==='{')stack.push({object:true,key:true,seen:new Set()});
            else if(part==='[')stack.push({object:false});
            else if(part==='}'||part===']')stack.pop();
            else if(part===','&&top?.object)top.key=true;
            else if(part.startsWith('"')&&top?.object&&top.key){
                const key=JSON.parse(part);need(!top.seen.has(key),'entry_input_invalid');top.seen.add(key);top.key=false;
            }
        }
        need(parsed&&typeof parsed==='object'&&!Array.isArray(parsed),'entry_input_invalid');
        if(manifestOperation!==null&&role==='manifest')manifestCapsule.stage(manifestOperation,parsed,records[records.length-1],expected);
        return parsed;
    }
    return {readOnce,metadata,decode,relative,guard,addMetadata:n=>{metadataBytes+=n;},
        metrics:()=>({capture_read_bytes:total,metadata_bytes:metadataBytes,known_owner_input_bytes:total+metadataBytes})};
}

function createEntrySourceOwner(trustedCodeMapsRoot,expectedInput,profilesInput,schemaInput,options,textOnly=false,operation=null,ownedRowOperation=null,manifestOperation=null,identityOperation=null,trace=null){
    const profile=textOnly?'private_compiler_text_owner':PROFILE;
    const registry=ownedRowOperation===null?(textOnly?textOwners:owners):rowOwners;
    let held=null,text=null,raw=null,record=null,candidate=null,settled=false;
    try{
        const now=options?.now||(()=>performance.now()),deadline=options?.deadline;
        need(typeof now==='function','entry_deadline');const started=now();
        need(Number.isFinite(started)&&Number.isFinite(deadline)&&started<deadline,'entry_deadline');
        need(typeof trustedCodeMapsRoot==='string'&&path.isAbsolute(trustedCodeMapsRoot),'entry_path_refused');
        const expectedData=dataCopy(expectedInput,'entry_expected_invalid');
        need(Object.values(expectedData).every(value=>typeof value==='string'||typeof value==='number'),'entry_expected_invalid');
        need(profilesInput&&typeof profilesInput==='object'&&profilesInput[profile]&&typeof profilesInput[profile]==='object','entry_policy_invalid');
        const first=profilesInput[profile],captureInput=profilesInput[first.capture_profile],imageInput=profilesInput[first.image_profile];
        need(captureInput&&typeof captureInput==='object'&&imageInput&&typeof imageInput==='object'&&imageInput.limits,'entry_policy_invalid');
        const ceiling=imageInput.limits.metadata_bytes;
        need(Number.isSafeInteger(ceiling)&&ceiling>0,'entry_policy_invalid');
        text=JSON.stringify({expected:expectedData,descriptor:first,capture:captureInput,image:imageInput,schema:schemaInput});
        const inputBytes=Buffer.byteLength(text,'utf8')+(textOnly?bytes(operation):0)+(ownedRowOperation===null?0:bytes(ownedRowOperation))+(trace?trace.reservation:0);need(inputBytes<=ceiling,'entry_budget');
        held=JSON.parse(text);text=null;
        const {expected,descriptor:d,capture,image,schema}=held,limit=image.limits;
        need(schema&&typeof schema==='object'&&schema.$defs?.policy&&schema.$defs?.expected,'entry_policy_invalid');
        try{
            validate(d,schema.$defs.policy,schema,'entry_policy_invalid');
            validate(expected,schema.$defs.expected,schema,'entry_expected_invalid');
        }catch(error){if(error instanceof StreamStagerRefusal)refuse(error.code==='entry_expected_invalid'?'entry_expected_invalid':'entry_policy_invalid');throw error;}
        need(d.version===(textOnly?'v1-private-compiler-text-owner':'v1-private-non-evaluating-compiler-entry')&&d.model===(textOnly?'single_declared_compiler_entry_text_only_v1':'single_synthetic_compiler_entry_v1')
            &&d.text_policy==='strict_utf8_preserve_bom_shebang_crlf_v1'&&d.root_authority==='trusted_first_party_invocation_only'
            &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>d[k]===false)
            &&d.schema_sha256===hash(schema)&&d.capture_policy_sha256===hash(capture)&&d.image_policy_sha256===hash(image),'entry_policy_invalid');
        need(expected.policy_sha256===hash(d)&&expected.schema_sha256===hash(schema)&&expected.capture_policy_sha256===hash(capture)
            &&expected.image_policy_sha256===hash(image)&&expected.owner_id===d.owner_id,'entry_identity');
        need([capture.max_file_bytes,capture.max_module_bytes,capture.max_total_bytes,limit.metadata_bytes,limit.image_bytes,limit.capture_ms]
            .every(n=>Number.isSafeInteger(n)&&n>0),'entry_policy_invalid');
        let last=started;
        need(deadline<=started+limit.capture_ms,'entry_deadline');
        const check=()=>{const time=now();need(Number.isFinite(time)&&time>=last&&time<deadline,'entry_deadline');last=time;};
        check();
        const root=fileOperation(()=>fs.realpathSync(trustedCodeMapsRoot));
        if(trace)trace.rootResolved(root);
        if(identityOperation)identityOperation.checkRoot(root);
        need(fileOperation(()=>fs.statSync(root)).isDirectory(),'entry_path_refused');
        const session=createCheckedReadSession(root,d,capture,limit,inputBytes,check,textOnly,operation,manifestOperation,expected,identityOperation?.sidecar||null,trace);
        const {readOnce,metadata,decode,relative,guard}=session;
        const provenance=metadata(d.provenance_relative_path,'provenance',expected.provenance_sha256);
        need(Array.isArray(provenance.vendored_dependencies),'entry_input_invalid');
        const matches=provenance.vendored_dependencies.filter(row=>row&&row.id===d.owner_id);
        need(matches.length===1,'entry_identity');const owner=matches[0];
        need(owner.locked_version===expected.compiler_version&&owner.lockfile_sha256===expected.lockfile_sha256,'entry_identity');
        const manifest=metadata(owner.manifest_path,'manifest',expected.manifest_sha256);
        const lock=metadata(owner.lockfile_path,'lockfile',expected.lockfile_sha256);
        need(manifest.dependencies?.[d.owner_id]===expected.compiler_version&&typeof owner.lockfile_package_key==='string'
            &&Object.hasOwn(lock.packages||{},owner.lockfile_package_key)&&lock.packages[owner.lockfile_package_key]?.version===expected.compiler_version,'entry_identity');
        const marker=relative(owner.runtime_package_path);
        if(trace)trace.packageMarker(marker);
        const packageRoot=fileOperation(()=>fs.realpathSync(path.dirname(path.join(root,...marker.split('/')))));
        if(trace)trace.packageResolved(packageRoot);
        const pkg=metadata(path.posix.basename(marker),'package',expected.package_sha256,packageRoot);
        need(pkg.name===d.owner_id&&pkg.version===expected.compiler_version,'entry_identity');
        const entry=relative(pkg.main);need(sha(entry)===expected.entry_relative_sha256,'entry_identity');
        raw=readOnce(packageRoot,entry,'entry',true);
        if(trace)trace.note('view_handoff','entry',raw.length,null);
        need(raw.length===expected.entry_bytes&&sha(raw)===expected.entry_sha256,'entry_identity');
        text=decode(raw);const observedTextHash=sha(text);need(observedTextHash===expected.text_sha256,'entry_identity');
        if(trace)trace.note('text_ready','entry',raw.length,text.length);
        session.addMetadata(bytes({text_sha256:expected.text_sha256,text_code_units:text.length}));guard();
        if(identityOperation){
            const receipt=identityOperation.finish(observedTextHash,text.length,session.metrics(),inputBytes);
            const use=Object.getOwnPropertyDescriptor(identityOperation,'use');
            need(use===undefined||Object.hasOwn(use,'value')&&typeof use.value==='function','entry_order');
            const result=use===undefined?receipt:use.value(text,receipt);
            record=raw=text=null;check();settled=true;return result;
        }
        record={raw,text,size_bytes:raw.length,raw_sha256:expected.entry_sha256,text_sha256:expected.text_sha256};
        const refs={context_sha256:hash(expected),entry_sha256:expected.entry_sha256,text_sha256:expected.text_sha256,
            policy_sha256:expected.policy_sha256,schema_sha256:expected.schema_sha256};
        const metrics=session.metrics();
        if(manifestOperation!==null)manifestCapsule.checkImage(manifestOperation,metrics);
        candidate=makeOwner(record,refs,metrics,d,check,registry,ownedRowOperation===null?operation:ownedRowOperation,trace);operation=ownedRowOperation=null;record=raw=text=null;check();need(registry.has(candidate),'entry_order');
        if(trace)trace.note('owner_publish','entry',metrics.capture_read_bytes,null);
        settled=true;return candidate;
    }finally{
        if(!settled){const row=registry.get(candidate);row?.dispose();}
        held=text=raw=record=candidate=operation=ownedRowOperation=null;
    }
}

function createCompilerEntryOwner(root,expected,profiles,schema,options){return createEntrySourceOwner(root,expected,profiles,schema,options);}

// A disjoint metadata transaction: it never enters any executable/text/row owner registry.
const entryIdentity=require('./compiler_entry_identity.cjs')._bindCompilerEntryIdentity(createEntrySourceOwner,CompilerEntryRefusal,samePath);
function observeSyntheticCompilerEntryIdentity(root,expected,profiles,schemas,options){return entryIdentity.observe(root,expected,profiles,schemas,options);}
function observeSyntheticCompilerEntryUse(root,expected,profiles,schemas,options){return entryIdentity.use(root,expected,profiles,schemas,options);}
const textConsumer=(()=>{
    const {copy,checked,sum,need,note,CompilerTextWindowRefusal,CompilerExportRecipeRefusal}=textWindow;
    const {recognize}=require('./compiler_source_recipe_cursor.cjs');
    const prepare=textWindow.prepare,active=new WeakMap(),capsules=new WeakMap();
    const object=v=>v!==null&&(typeof v==='object'||typeof v==='function');
function disposeCapsule(token){const row=capsules.get(token);if(!row)return false;row.observation=null;row.state='DISPOSED';capsules.delete(token);return true;}
function consumeCapsule(token,expected){
    const row=capsules.get(token);need(row,'text_order');if(row.state!=='HELD'){row.control.poisoned=true;throw new CompilerTextWindowRefusal('text_order');}row.state='CONSUMING';
    try{
        need(hash(copy(expected,'text_expected_invalid',row.ceiling))===row.expectedHash,'text_identity');row.check();
        need(row.state==='CONSUMING'&&row.observation,'text_order');row.control.stage='consume';row.control.consumptions++;
        const out=copy(row.observation,'text_result_invalid',row.ceiling);row.check();
        need(row.state==='CONSUMING'&&row.observation!==null&&capsules.get(token)===row,'text_order');return out;
    }finally{disposeCapsule(token);}
}
function observe(owner,expectedInput,profilesInput,schemasInput,options){
    const previous=active.get(owner);if(previous){previous.poisoned=true;throw new CompilerTextWindowRefusal('text_order');}
    const control={stage:'admission',poisoned:false,consumptions:0};let registered=false,row=null,op=null,snapshot=null,staged=null,token=null,result=null,primary=null,failed=false,inBorrow=false;
    let wholeBuffer=null,viewBuffer=null,viewText=null;
    try{
        need(object(owner),'text_order');row=textOwners.get(owner);need(row&&row.operation,'text_order');active.set(owner,control);registered=true;
        const now=options?.now||(()=>performance.now()),deadline=options?.deadline,started=now();let last=started;
        need(Number.isFinite(started)&&Number.isFinite(deadline)&&started<deadline,'text_deadline');
        const check=()=>{const t=now();need(!control.poisoned,'text_order');need(Number.isFinite(t)&&t>=last&&t<deadline,'text_deadline');last=t;row.check();
            if(inBorrow){const s=owner.snapshot();need(s.state==='BORROWING'&&s.entry_owner_registered&&s.entry_input_verified,'text_order');}};
        control.stage='validation';op=prepare(null,expectedInput,profilesInput,schemasInput);need(hash(op)===hash(row.operation),'text_identity');
        const {expected:e,descriptor:d,image,schemas,pins}=op,s=e.selection,w=d.whole_limits,l=d.limits;
        need(deadline<=started+image.limits.capture_ms,'text_deadline');check();
        let metadataBytes=0,reserved=0,total=0,units=0;
        const metrics={scan_code_units:0,token_count:0,work_units:0,fact_bytes:0,name_utf8_bytes:0,gap_bytes:0,
            whole_verification_code_units:0,whole_verification_utf8_bytes:0,whole_verification_work_units:0,selection_work_units:0,selection_work_reserved_units:0,view_reserved_utf8_bytes:0};
        row.borrow(e.entry,input=>{
            inBorrow=true;control.stage='entry_identity';check();snapshot=owner.snapshot();
            need(input.raw_sha256===e.entry.entry_sha256&&input.text_sha256===e.entry.text_sha256&&input.text.length===s.source_code_units,'text_identity');
            units=input.text.length;const v=s.view_code_units,maxViewBytes=sum(v,v,v,v);control.stage='window_budget';
            need(units<=w.source_code_units&&input.size_bytes<=w.verification_utf8_bytes&&v<=l.scan_code_units
                &&sum(units,input.size_bytes)<=w.verification_work_units&&sum(4,v,v,maxViewBytes)<=w.selection_work_units,'text_budget');
            metrics.selection_work_reserved_units=sum(4,v,v,maxViewBytes);metadataBytes=sum(bytes(op),bytes(op),bytes(snapshot));
            need(metadataBytes<=image.limits.metadata_bytes,'text_budget');
            reserved=sum(l.fact_bytes,l.fact_bytes,l.name_utf8_bytes,l.name_utf8_bytes,l.gap_bytes,l.cursor_bytes,input.size_bytes,v,v,maxViewBytes);
            total=sum(snapshot.known_owner_input_bytes,units,units,metadataBytes,reserved,l.summary_bytes);need(total<=image.limits.image_bytes,'text_budget');check();
            control.stage='selection';
            for(const at of [s.start_utf16,s.end_utf16])if(at>0&&at<units){metrics.selection_work_units=sum(metrics.selection_work_units,2);
                const a=input.text.charCodeAt(at-1),b=input.text.charCodeAt(at);need(!(a>=0xd800&&a<=0xdbff&&b>=0xdc00&&b<=0xdfff),'text_expected_invalid');}
            check();control.stage='whole_verification';wholeBuffer=Buffer.from(input.text,'utf8');
            need(wholeBuffer.length===input.size_bytes&&sha(wholeBuffer)===e.entry.text_sha256,'text_identity');
            metrics.whole_verification_code_units=units;metrics.whole_verification_utf8_bytes=input.size_bytes;metrics.whole_verification_work_units=sum(units,input.size_bytes);wholeBuffer=null;check();
            control.stage='selection';viewText=input.text.slice(s.start_utf16,s.end_utf16);viewBuffer=Buffer.from(viewText,'utf8');
            need(viewText.length===v&&viewBuffer.length===s.view_utf8_bytes&&sha(viewBuffer)===s.view_text_sha256,'text_identity');
            metrics.selection_work_units=sum(metrics.selection_work_units,v,v,viewBuffer.length);metrics.view_reserved_utf8_bytes=maxViewBytes;viewBuffer=null;check();
            control.stage='scan';staged=recognize(viewText,l,check,metrics,true,{origin:s.start_utf16,end:s.end_utf16,context:e.entry.context_sha256,selection:e.selection_sha256});viewText=null;
            if(staged.facts)checked(staged.facts,schemas.text_window.$defs.facts,schemas.text_window,'text_result_invalid');
            checked(staged.observation,schemas.text_window.$defs.observation,schemas.text_window,'text_result_invalid');check();
            // Fixed consumer returns no text, facts, callable or thenable to the owner.
        });
        inBorrow=false;control.stage='settlement';check();const after=owner.snapshot();
        need(after.state==='DISPOSED'&&!after.entry_owner_registered&&!after.entry_input_verified&&after.retained_entry_bytes===0&&after.retained_text_code_units===0,'text_order');
        token=Object.freeze({kind:'private_declared_text_window_capsule'});capsules.set(token,{state:'HELD',observation:staged.observation,expectedHash:hash(e),ceiling:image.limits.metadata_bytes,control,check});staged=null;
        control.stage='capsule_ready';check();const observation=consumeCapsule(token,e);
        need(!capsules.has(token)&&control.consumptions===1,'text_order');control.stage='result';
        result={version:d.version,status:observation.view_syntax_complete?d.complete_status:d.incomplete_status,entry:{...e.entry},expected_sha256:hash(e),...pins,
            owner_snapshot_sha256:hash(snapshot),observation_sha256:hash(observation),observation,...metrics,
            entry_capture_read_bytes:snapshot.capture_read_bytes,entry_known_input_bytes:snapshot.known_owner_input_bytes,source_text_code_units:units,source_text_reserved_bytes:sum(units,units),
            observer_metadata_bytes:metadataBytes,observer_reserved_bytes:reserved,summary_reserved_bytes:l.summary_bytes,known_inputs_reserved_bytes:total,
            entry_borrows:1,capsule_consumptions:1,entry_owner_disposed:true,capsule_owner_disposed:true,local_source_refs_cleared:true,
            text_window_candidate_observed:true,expected_observation_compared:false,owned_source_units:units,scanned_units:metrics.scan_code_units,
            window:{start_utf16:s.start_utf16,end_utf16:s.end_utf16,view_code_units:s.view_code_units,view_utf8_bytes:s.view_utf8_bytes,view_text_sha256:s.view_text_sha256,
                selection_sha256:e.selection_sha256,provenance_sha256:s.provenance_sha256,global_start_utf8:null,global_end_utf8:null,global_line:null,global_column:null},
            ...Object.fromEntries(['whole_file_coverage','helper_identity_resolved','target_binding_resolved','enclosing_source_syntax_verified','actual_callsite_context_verified','global_byte_line_mapping_verified',
                'actual_export_set_complete','runtime_descriptor_observed','runtime_prototype_observed','runtime_integrity_observed','compiler_loaded','loaded_code_attested','native_execution_attested',
                'source_freshness_verified','snapshot_bound','image_admitted','production_enabled','semantic_lineage','cache_eligible','publication_authority'].map(k=>[k,false])),
            entry_evaluations:0,getter_invocations:0,api_invocations:0,backend_invocations:0,compiled_heap_bytes:null,runtime_heap_bytes:null,peak_rss_bytes:null};
        checked(result,schemas.text_window.$defs.result,schemas.text_window,'text_result_invalid');need(bytes(result)<=l.summary_bytes,'text_budget');
        Object.freeze(result.entry);Object.freeze(result.window);Object.freeze(result.observation.gap);Object.freeze(result.observation);Object.freeze(result);check();
    }catch(e){primary=note(e instanceof CompilerExportRecipeRefusal?Object.assign(new CompilerTextWindowRefusal(e.code==='recipe_budget'?'text_budget':'text_identity'),{cause:e}):e,control.stage);failed=true;result=null;}
    finally{
        const cleanup=[];try{disposeCapsule(token);}catch(e){cleanup.push(note(e,'cleanup'));}try{if(row&&textOwners.get(owner)===row)row.dispose();}catch(e){cleanup.push(note(e,'cleanup'));}
        row=op=snapshot=staged=token=wholeBuffer=viewBuffer=viewText=null;if(registered)active.delete(owner);
        if(cleanup.length){primary=note(new AggregateError(failed?[primary,...cleanup]:cleanup,'compiler_text_window_cleanup_failed',failed?{cause:primary}:undefined),'cleanup');failed=true;}
    }
    if(failed)throw primary;return result;
}
return {observe,consumeCapsule,disposeCapsule};
})();

function createCompilerTextWindowOwner(root,expected,profiles,schemas,options){
    const operation=textWindow.prepare(root,expected,profiles,schemas);
    const admitted={private_compiler_text_owner:operation.entry,compiler_library_inputs:operation.capture,private_input_image_experiment:operation.image};
    return createEntrySourceOwner(root,operation.expected.entry_expected,admitted,operation.schemas.entry,options,true,operation);
}
function observeCompilerTextWindowCandidate(owner,expected,profiles,schemas,options){
    return textConsumer.observe(owner,expected,profiles,schemas,options);
}

const rowConsumer=(()=>{
    const {prepare,reserve,checked,sum,need,note}=ownedRow,active=new WeakMap(),capsules=new WeakMap();
    const copy=textWindow.copy;
    function disposeCapsule(token){const row=capsules.get(token);if(!row)return false;row.fragment=null;row.state='DISPOSED';capsules.delete(token);return true;}
    function consumeCapsule(token,expected){
        const row=capsules.get(token);need(row,'owned_row_order');
        if(row.state!=='HELD'){row.control.poisoned=true;throw new ownedRow.CompilerOwnedRowFragmentRefusal('owned_row_order');}
        row.state='CONSUMING';
        try{
            need(hash(copy(expected,'owned_row_expected',row.ceiling))===row.expectedHash,'owned_row_identity');row.check();
            need(row.state==='CONSUMING'&&row.fragment,'owned_row_order');row.control.stage='consume';row.control.consumptions++;
            const result=copy(row.fragment,'owned_row_result',row.ceiling);row.check();
            need(row.state==='CONSUMING'&&row.fragment!==null&&capsules.get(token)===row,'owned_row_order');return result;
        }finally{disposeCapsule(token);}
    }
    function observe(owner,expectedInput,profilesInput,schemasInput,options){
        const prior=active.get(owner);if(prior){prior.poisoned=true;throw new ownedRow.CompilerOwnedRowFragmentRefusal('owned_row_order');}
        const control={stage:'admission',poisoned:false,consumptions:0};
        let row=null,registered=false,op=null,snapshot=null,view=null,buffer=null,fragment=null,token=null,result=null,primary,failed=false,inBorrow=false;
        try{
            row=rowOwners.get(owner);need(row&&row.operation,'owned_row_order');active.set(owner,control);registered=true;
            const now=options?.now||(()=>performance.now()),deadline=options?.deadline;need(typeof now==='function','owned_row_deadline');
            const started=now();let last=started;
            need(Number.isFinite(started)&&Number.isFinite(deadline)&&started<deadline,'owned_row_deadline');
            const check=()=>{const t=now();need(!control.poisoned,'owned_row_order');need(Number.isFinite(t)&&t>=last&&t<deadline,'owned_row_deadline');last=t;row.check();
                if(inBorrow){const state=owner.snapshot();need(state.state==='BORROWING'&&state.entry_owner_registered&&state.entry_input_verified,'owned_row_order');}};
            control.stage='validation';op=prepare(null,expectedInput,profilesInput,schemasInput);need(hash(op)===hash(row.operation),'owned_row_identity');
            need(deadline<=started+op.descriptor.limits.operation_ms,'owned_row_deadline');check();
            const e=op.expected,b=op.base.expected,s=b.selection,d=op.descriptor;
            let ledger=null,selectionWork=0;
            row.borrow(b.entry,input=>{
                inBorrow=true;control.stage='whole_identity';check();snapshot=owner.snapshot();
                need(input.raw_sha256===b.entry.entry_sha256&&input.text_sha256===b.entry.text_sha256&&input.text.length===s.source_code_units,'owned_row_identity');
                control.stage='joined_budget';ledger=reserve(op,snapshot);check();
                for(const at of [s.start_utf16,s.end_utf16])if(at>0&&at<input.text.length){selectionWork=sum(selectionWork,2);
                    const a=input.text.charCodeAt(at-1),z=input.text.charCodeAt(at);need(!(a>=0xd800&&a<=0xdbff&&z>=0xdc00&&z<=0xdfff),'owned_row_expected');}
                control.stage='whole_verification';buffer=Buffer.from(input.text,'utf8');
                need(buffer.length===input.size_bytes&&sha(buffer)===b.entry.text_sha256,'owned_row_identity');buffer=null;check();
                control.stage='selection';view=input.text.slice(s.start_utf16,s.end_utf16);buffer=Buffer.from(view,'utf8');
                need(view.length===s.view_code_units&&buffer.length===s.view_utf8_bytes&&sha(buffer)===s.view_text_sha256,'owned_row_identity');
                selectionWork=sum(selectionWork,2*s.view_code_units,buffer.length);buffer=null;check();
                control.stage='fragment';fragment=observeSuppliedCompilerRowFragment(view,e.fragment_expected,
                    {private_compiler_row_fragment:op.fragmentContext.descriptor,private_compiler_text_window:{limits:op.fragmentContext.windowLimits},private_input_image_experiment:op.fragmentContext.image},
                    op.schemas.row_fragment,{now:()=>{check();return last;},deadline});view=null;
                need(fragment.known_inputs_reserved_bytes===op.fragment_reserved_bytes&&bytes(fragment)<=d.limits.capsule_bytes,'owned_row_budget');check();
            });
            inBorrow=false;control.stage='settlement';check();const after=owner.snapshot();
            need(after.state==='DISPOSED'&&!after.entry_owner_registered&&!after.entry_input_verified&&after.retained_entry_bytes===0&&after.retained_text_code_units===0,'owned_row_order');
            token=Object.freeze({kind:'private_owned_row_fragment_capsule'});
            capsules.set(token,{state:'HELD',fragment,expectedHash:hash(e),ceiling:d.limits.metadata_bytes,control,check});fragment=null;
            control.stage='capsule_ready';check();fragment=consumeCapsule(token,e);need(!capsules.has(token)&&control.consumptions===1,'owned_row_order');
            control.stage='result';result={version:d.version,status:fragment.observation.fragment_syntax_complete?d.complete_status:d.incomplete_status,
                integration_policy_sha256:hash(d),integration_schema_sha256:hash(op.schemas.owned_row_fragment),expected_sha256:hash(e),
                base_request_sha256:e.base_request_sha256,fragment_expected_sha256:e.fragment_expected_sha256,entry:{...b.entry},
                selection:{...s},selection_sha256:b.selection_sha256,owner_snapshot_sha256:hash(snapshot),fragment_result:fragment,fragment_result_sha256:hash(fragment),
                ...ledger,entry_capture_read_bytes:snapshot.capture_read_bytes,entry_known_input_bytes:snapshot.known_owner_input_bytes,
                source_text_reserved_bytes:2*s.source_code_units,whole_verification_code_units:s.source_code_units,
                whole_verification_utf8_bytes:b.entry_expected.entry_bytes,whole_verification_work_units:sum(s.source_code_units,b.entry_expected.entry_bytes),
                selection_work_units:selectionWork,selected_text_reserved_bytes:2*s.view_code_units,capsule_reserved_bytes:2*d.limits.capsule_bytes,summary_reserved_bytes:d.limits.summary_bytes,
                entry_borrows:1,capsule_consumptions:1,entry_owner_disposed:true,capsule_owner_disposed:true,local_source_refs_cleared:true,absolute_mapping:null,
                ...Object.fromEntries(['source_freshness_verified','read_authority_granted','filesystem_capture_attested','table_membership_verified','table_complete','actual_callsite_context_verified',
                    'binding_resolved','actual_export_set_complete','input_code_evaluated','compiler_loaded','production_enabled','semantic_lineage','cache_eligible','publication_authority'].map(k=>[k,false]))};
            checked(result,'result',op.schemas.owned_row_fragment,'owned_row_result');need(bytes(result)<=d.limits.summary_bytes,'owned_row_budget');
            control.stage='publish';check();Object.freeze(fragment.flags);if(fragment.observation.gap)Object.freeze(fragment.observation.gap);
            Object.freeze(fragment.observation);Object.freeze(fragment);Object.freeze(result.entry);Object.freeze(result.selection);Object.freeze(result);check();
        }catch(error){primary=note(error,control.stage);failed=true;result=null;}
        finally{
            const cleanup=[];try{disposeCapsule(token);}catch(e){cleanup.push(note(e,'cleanup'));}
            try{if(row&&rowOwners.get(owner)===row)row.dispose();}catch(e){cleanup.push(note(e,'cleanup'));}
            row=op=snapshot=view=buffer=fragment=token=null;if(registered)active.delete(owner);
            if(cleanup.length){primary=note(new AggregateError(failed?[primary,...cleanup]:cleanup,'compiler_owned_row_cleanup_failed',failed?{cause:primary}:undefined),'cleanup');failed=true;}
        }
        if(failed)throw primary;return result;
    }
    return {observe,consumeCapsule,disposeCapsule};
})();
function createCompilerOwnedRowFragmentOwner(root,expected,profiles,schemas,options){
    let owner=null,settled=false;
    try{
        const now=options?.now||(()=>performance.now()),deadline=options?.deadline;ownedRow.need(typeof now==='function','owned_row_deadline');
        const started=now();let last=started;
        ownedRow.need(Number.isFinite(started)&&Number.isFinite(deadline)&&started<deadline,'owned_row_deadline');
        const guarded=()=>{const t=now();ownedRow.need(Number.isFinite(t)&&t>=last&&t<deadline,'owned_row_deadline');last=t;return t;};
        const op=ownedRow.prepare(root,expected,profiles,schemas),base=op.base;
        ownedRow.need(deadline<=started+op.descriptor.limits.operation_ms,'owned_row_deadline');guarded();
        const admitted={private_compiler_text_owner:base.entry,compiler_library_inputs:base.capture,private_input_image_experiment:base.image};
        owner=createEntrySourceOwner(root,base.expected.entry_expected,admitted,base.schemas.entry,{now:guarded,deadline},true,base,op);
        guarded();ownedRow.need(rowOwners.has(owner),'owned_row_order');settled=true;return owner;
    }catch(e){throw ownedRow.note(e,'construction');}
    finally{if(!settled)rowOwners.get(owner)?.dispose();}
}
function observeCompilerOwnedRowFragment(owner,expected,profiles,schemas,options){return rowConsumer.observe(owner,expected,profiles,schemas,options);}

class ManifestCapsuleRefusal extends Error {constructor(code){super(code);this.code=code;}}
const manifestCapsule=(()=>{
    // Fixed synthetic transaction only. No capsule constructor/consumer is exported.
    const joint=require('./compiler_canonical_envelope.cjs'),capsules=new WeakMap(),metadataCapsules=new WeakMap(),metadataTransactions=new WeakMap(),historicalCapsules=new WeakMap(),historicalTransactions=new WeakMap(),failures=new WeakMap(),secondaryFailures=new WeakMap();
    let active=null;
    const get=Object.getOwnPropertyDescriptor,own=Object.hasOwn;
    const typed=Object.getPrototypeOf(Uint8Array.prototype),slot=k=>get(typed,k).get;
    const viewLength=slot('byteLength'),viewOffset=slot('byteOffset'),viewBacking=slot('buffer');
    const abLength=get(ArrayBuffer.prototype,'byteLength').get,resizable=get(ArrayBuffer.prototype,'resizable')?.get;
    const bufferPrototype=Buffer.prototype,from=Buffer.from,hashNative=crypto.createHash;
    const fail=code=>{throw new ManifestCapsuleRefusal(code);},requireValue=(v,code)=>{if(!v)fail(code);};
    const object=v=>v!==null&&(typeof v==='object'||typeof v==='function');
    function field(v,k){
        requireValue(v&&typeof v==='object'&&!types.isProxy(v),'manifest_context');
        const d=get(v,k);requireValue(d&&own(d,'value'),'manifest_context');return d.value;
    }
    function keys(v,names){requireValue(Object.keys(v).length===names.length&&names.every(k=>own(v,k)),'manifest_context');}
    function hashBytes(v){return hashNative('sha256').update(v).digest('hex');}
    function capture(v,c,cap,seen=new Set(),depth=0,budget={bytes:0,nodes:0}){
        c.step('prepare');requireValue(depth<=32&&++budget.nodes<=cap,'manifest_context_budget');
        if(v===null||['number','boolean','string'].includes(typeof v)){
            requireValue(typeof v!=='number'||Number.isFinite(v),'manifest_context');
            budget.bytes+=typeof v==='string'?Buffer.byteLength(v,'utf8'):8;
            requireValue(budget.bytes<=cap,'manifest_context_budget');return v;
        }
        requireValue(v&&typeof v==='object'&&!types.isProxy(v)&&!seen.has(v),'manifest_context');
        requireValue(Array.isArray(v)||Object.getPrototypeOf(v)===Object.prototype||Object.getPrototypeOf(v)===null,'manifest_context');
        const ds=Object.getOwnPropertyDescriptors(v),names=Reflect.ownKeys(ds);
        requireValue(names.length<=cap-budget.nodes&&names.every(k=>typeof k==='string'&&own(ds[k],'value')),'manifest_context');
        seen.add(v);
        try{
            if(Array.isArray(v)){
                const n=ds.length?.value;requireValue(Number.isSafeInteger(n)&&n>=0&&names.length===n+1,'manifest_context');
                return Array.from({length:n},(_,i)=>{requireValue(own(ds,String(i)),'manifest_context');return capture(ds[i].value,c,cap,seen,depth+1,budget);});
            }
            return Object.fromEntries(names.map(k=>{
                budget.bytes+=Buffer.byteLength(k,'utf8');requireValue(budget.bytes<=cap,'manifest_context_budget');
                return [k,capture(ds[k].value,c,cap,seen,depth+1,budget)];
            }));
        }finally{seen.delete(v);}
    }
    function boundedBuffer(v,cap){
        requireValue(v&&typeof v==='object'&&!types.isProxy(v)&&types.isUint8Array(v),'manifest_output');
        const n=viewLength.call(v),offset=viewOffset.call(v),backing=viewBacking.call(v);
        requireValue(!types.isSharedArrayBuffer(backing),'manifest_output');const size=abLength.call(backing);
        requireValue((!resizable||!resizable.call(backing))&&Object.getPrototypeOf(v)===bufferPrototype
            &&n>0&&n<=cap&&offset>=0&&offset+n<=size,'manifest_output');
        // Detached slots/native hashing failures refuse; no permanent non-detachability claim.
        return n;
    }
    function fixedValidate(v,d,s,c,group){joint._validateCanonicalMetadataValue(v,d,s,{need:requireValue,step:()=>c.step(group)},true);}
    function disposeCapsule(token){
        const registry=historicalCapsules.has(token)?historicalCapsules:metadataCapsules.has(token)?metadataCapsules:capsules;const row=registry.get(token);if(!row)return;
        row.parsed=row.record=row.parent=null;row.state='ABORTED';registry.delete(token);
    }
    function consume(token,c){
        const registry=c.historicalMetadata?historicalCapsules:c.metadataOnly?metadataCapsules:capsules,row=registry.get(token);c.consume_attempts++;
        requireValue(row&&row.parent===c.token&&row.state==='STAGED'&&!c.poisoned,'manifest_order');
        row.state='CONSUMING';c.stage='child';let out=null,body=null,captured=null,wire=null,frame=null;
        try{
            c.check();requireValue(row.role==='manifest'&&row.record.role==='manifest'
                &&row.record.raw_sha256===c.manifest_raw_expected
                &&row.record.raw_sha256===row.expected_raw_sha256,'manifest_identity');
            const x=c.ctx,expected=x.expected.joint_expected,p=x.profiles.private_compiler_metadata_joint;
            const resources={basis:x.profiles.private_compiler_owned_view_basis.limits,planner:x.profiles.private_compiler_owned_view_plan.limits};
            c.child_attempts++;out=joint.encodeSyntheticCanonicalMetadataWithStructure(row.parsed,expected,p,resources,x.schemas.joint,
                {now:()=>{c.check();return c.last;},deadline:c.deadline});c.child_returns++;
            c.check();requireValue(row.state==='CONSUMING'&&registry.get(token)===row&&!c.poisoned,'manifest_order');
            c.stage='verification';c.charged_work=c.wrapper_work;c.attempted_work=c.charged_work;
            requireValue(out&&typeof out==='object'&&!types.isProxy(out)
                &&[Object.prototype,null].includes(Object.getPrototypeOf(out)),'manifest_output');
            const ds=Object.getOwnPropertyDescriptors(out),names=Reflect.ownKeys(ds);
            requireValue(names.length===5&&['payload','structure','receipt','receiptSha256','settlement'].every(k=>own(ds,k)&&own(ds[k],'value'))
                &&names.every(k=>typeof k==='string'),'manifest_output');
            const payload=ds.payload.value,structure=ds.structure.value,receipt=ds.receipt.value;
            const payloadBytes=boundedBuffer(payload,p.sublimits.payload_bytes),structureBytes=boundedBuffer(structure,c.R);
            const receiptBytes=boundedBuffer(receipt,c.O);
            wire=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(receipt);c.receipt_parse_attempts++;
            body=JSON.parse(wire);c.receipt_parse_returns++;c.check();
            const receiptBudget={bytes:0,nodes:0};
            captured=capture(body,{step:()=>c.step('receipt')},8*c.O,new Set(),0,receiptBudget);
            fixedValidate(captured,x.schemas.joint.$defs.receipt,x.schemas.joint,c,'receipt');
            const r=captured,terminal=capture(ds.settlement.value,{step:()=>c.step('receipt')},8*c.O,new Set(),0,receiptBudget);
            keys(terminal,['calls','clock_ok','private_refs_cleared','primitive_steps','metadata_byte_work_units']);
            requireValue(terminal.clock_ok===true&&terminal.private_refs_cleared===true
                &&Number.isSafeInteger(terminal.metadata_byte_work_units)&&terminal.metadata_byte_work_units===r.metadata_byte_work_units
                &&terminal.metadata_byte_work_units<=c.child_work&&Number.isSafeInteger(terminal.primitive_steps)
                &&terminal.primitive_steps>=r.primitive_steps&&terminal.primitive_steps<=c.child_steps,'manifest_output');
            fixedValidate(terminal.calls,x.schemas.joint.$defs.calls,x.schemas.joint,c,'receipt');
            for(const k of ['version','invocation_id','policy_sha256','schema_sha256','resource_sha256','intent_sha256'])
                requireValue(r[k]===expected[k],'manifest_identity');
            requireValue(r.status===p.status&&r.pair_identity_scheme===p.pair_identity_scheme,'manifest_identity');
            for(const k of ['source_refs','component_refs'])for(const name of Object.keys(expected[k]))
                requireValue(r[k][name]===expected[k][name],'manifest_identity');
            for(const k of ['limits','sublimits'])for(const name of Object.keys(p[k]))requireValue(r[k][name]===p[k][name],'manifest_identity');
            requireValue(Object.values(r.flags).every(v=>v===false)&&r.full_cost_bound===null&&r.full_capture_failure_work===null&&r.physical_heap_rss===null,'manifest_output');
            const rel=r.relationship;
            requireValue(rel.method==='one_private_snapshot_one_canonical_emit_v1'&&rel.content_scope==='strict_captured_synthetic_value_only'
                &&rel.object_order==='raw_utf8_keys_v1'&&rel.traversal==='canonical_depth_first_preorder_v1'
                &&rel.layout_construction_completed===true&&rel.layout_validator_completed===false
                &&rel.raw_json_identity_bound===false&&rel.external_origin_authenticated===false,'manifest_output');
            for(const k of ['capture_attempted','capture_completed','payload_encode_attempted','payload_encode_completed','payload_hash_attempted',
                'payload_hash_completed','structure_write_attempted','structure_write_completed','structure_hash_attempted','structure_hash_completed',
                'pair_hash_attempted','pair_hash_completed','receipt_encode_attempted','receipt_encode_completed','receipt_hash_attempted','receipt_hash_completed'])
                requireValue(terminal.calls[k]===1,'manifest_output');
            requireValue(terminal.calls.context_copy===1&&terminal.calls.context_encode===3&&terminal.calls.context_hash===3
                &&r.calls.receipt_hash_completed===0&&r.calls.receipt_hash_attempted===0&&r.calls.receipt_encode_completed===0,'manifest_output');
            const postReceipt=['receipt_encode_completed','receipt_hash_attempted','receipt_hash_completed'];
            requireValue(Object.keys(r.calls).every(k=>postReceipt.includes(k)||r.calls[k]===terminal.calls[k]),'manifest_output');
            requireValue(r.payload_bytes===payloadBytes&&r.structure_bytes===structureBytes&&r.structure_capacity_bytes===c.R,'manifest_output');
            c.step('protocol');const receiptHash=hashBytes(receipt),payloadHash=hashBytes(payload),structureHash=hashBytes(structure);
            requireValue(receiptHash===ds.receiptSha256.value&&payloadHash===r.payload_sha256&&structureHash===r.structure_sha256,'manifest_identity');
            frame=Buffer.alloc(171);c.step('frame',171);
            frame.write('SAGE_METADATA_CONTENT_STRUCTURE_V1\0',0,'ascii');frame.writeUInt32LE(payloadBytes,35);
            frame.write(payloadHash,39,64,'ascii');frame.writeUInt32LE(structureBytes,103);frame.write(structureHash,107,64,'ascii');
            const pairHash=hashBytes(frame);requireValue(pairHash===r.joint_pair_sha256,'manifest_identity');c.check();
            c.association={raw_manifest:{...row.record,expected_sha256:row.expected_raw_sha256},
                pair:{payload_sha256:payloadHash,payload_bytes:payloadBytes,structure_sha256:structureHash,structure_bytes:structureBytes,
                    joint_pair_sha256:pairHash,receipt_sha256:receiptHash,receipt_bytes:receiptBytes,
                    policy_sha256:r.policy_sha256,schema_sha256:r.schema_sha256,resource_sha256:r.resource_sha256,
                    source_refs:{...r.source_refs},component_refs:{...r.component_refs},intent_sha256:r.intent_sha256},
                child:{calls:{...terminal.calls},metadata_byte_work_units:terminal.metadata_byte_work_units,primitive_steps:terminal.primitive_steps}};
            row.state='CONSUMED';c.consumptions++;
        }finally{
            if(row){row.parsed=row.record=row.parent=null;if(row.state!=='CONSUMED')row.state='ABORTED';}
            registry.delete(token);out=body=captured=wire=frame=null;
        }
    }
    function stage(c,parsed,record,expected){
        c.stage='capsule';c.check();
        requireValue(active===c&&c.mints===0&&c.consume_attempts===0&&!c.poisoned,'manifest_order');
        requireValue(record.role==='manifest'&&record.raw_sha256===expected.manifest_sha256
            &&expected.manifest_sha256===c.manifest_raw_expected,'manifest_identity');
        const token=Object.freeze({toJSON(){throw new TypeError('manifest_capsule_not_serializable');},
            [Symbol.for('nodejs.util.inspect.custom')](){return '<private manifest capsule>';}});
        c.capsule=token;c.mints++;
        (c.historicalMetadata?historicalCapsules:c.metadataOnly?metadataCapsules:capsules).set(token,{state:'STAGED',role:'manifest',parent:c.token,parsed,record,
            expected_raw_sha256:expected.manifest_sha256});
        consume(token,c);c.check();
    }

    function metadataSeed(c,p,s){
        const e=c.ctx.expected,read=c.ctx.profiles.private_compiler_entry_owner;
        return {child_expected:Object.fromEntries(Object.entries(e.joint_expected).filter(([k])=>k!=='intent_sha256')),
            compiler_version:e.compiler_version,invocation_id:e.invocation_id,owner_id:e.owner_id,
            path_policy:{owner_id:read.owner_id,relative_path_pattern:read.relative_path_pattern},
            policy:p,policy_schema:s,scope:e.scope};
    }
    function prepareMetadata(root,c,p,basis){
        const x=c.ctx,e=x.expected,a=e.scope,l=p.scope_limits,capturePolicy=x.profiles.compiler_library_inputs,image=x.profiles.private_input_image_experiment;
        const readPolicy=x.profiles.private_compiler_entry_owner;
        requireValue(typeof root==='string'&&path.isAbsolute(root),'manifest_scope');
        const normalized=path.resolve(root),rootText=process.platform==='win32'?normalized.toLowerCase():normalized;
        keys(a,['decision_ref','max_delivered_bytes','max_open_attempts','max_total_raw_bytes','principal_ref','purpose','roles','root_scheme','root_text','unassigned_headroom_bytes']);
        requireValue(a.purpose===p.scope&&a.root_scheme==='node_resolved_absolute_win32_casefold_text_seed_v1'
            &&a.root_text===rootText&&rootText.length<=l.root_chars,'manifest_scope');
        const ascii=(v,n)=>typeof v==='string'&&v.length>0&&v.length<=n&&/^[\x20-\x7e]+$/.test(v);
        requireValue(ascii(a.principal_ref,96)&&ascii(a.decision_ref,96)&&ascii(e.owner_id,96)&&ascii(e.compiler_version,96)
            &&ascii(e.invocation_id,96)&&e.invocation_id===e.joint_expected.invocation_id&&e.owner_id===readPolicy.owner_id,'manifest_scope');
        requireValue(basis.metadata_roles.length===4&&!basis.metadata_roles.includes(basis.entry_role),'manifest_scope');
        keys(a.roles,basis.metadata_roles);
        requireValue(['provenance','manifest','lockfile','installed_package'].every(k=>own(a.roles,k)),'manifest_scope');
        requireValue(l.raw_bytes>0&&l.raw_bytes<=capturePolicy.max_file_bytes&&l.opens===basis.metadata_roles.length
            &&l.delivered_bytes<=l.opens*(l.raw_bytes+1),'manifest_scope');
        c.root_normalized=normalized;c.metadata_records=[];c.role_paths={};c.record_primitive=0;
        c.reads={open_attempts:0,open_returns:0,open_unknown:0,read_attempts:0,read_returns:0,read_unknown:0,
            known_delivered_bytes:0,close_attempts:0,close_returns:0,close_unknown:0,secondary_cleanup_errors:0};
        c.secondary_errors=[];c.manifest_raw_expected=a.roles.manifest.expected_sha256;
        let rawTotal=0,reviver=0;
        for(const role of basis.metadata_roles){
            c.step('records');const r=a.roles[role];keys(r,['expected_sha256','max_raw_bytes','relative_path']);
            requireValue(typeof r.expected_sha256==='string'&&/^[a-f0-9]{64}$/.test(r.expected_sha256)
                &&r.expected_sha256!=='0'.repeat(64)&&Number.isSafeInteger(r.max_raw_bytes)&&r.max_raw_bytes>0&&r.max_raw_bytes<=l.raw_bytes
                &&ascii(r.relative_path,l.path_chars)&&!r.relative_path.includes('\\'),'manifest_scope');
            rawTotal+=r.max_raw_bytes;reviver+=3*r.max_raw_bytes*(1+String(r.max_raw_bytes).length);
        }
        requireValue(Number.isSafeInteger(a.unassigned_headroom_bytes)&&a.unassigned_headroom_bytes>=0
            &&a.max_total_raw_bytes===rawTotal&&a.max_open_attempts===l.opens
            &&a.max_delivered_bytes===rawTotal+l.opens+a.unassigned_headroom_bytes
            &&a.max_delivered_bytes<=l.delivered_bytes&&rawTotal<=capturePolicy.max_total_bytes,'manifest_scope');
        c.capture_named_partial=rawTotal+l.opens+4*rawTotal+8*rawTotal+8*rawTotal+reviver;
        c.known_image_bytes=c.capture_named_partial+c.named_upper;
        requireValue(Number.isSafeInteger(c.known_image_bytes)&&c.known_image_bytes<=Math.min(p.limits.image_bytes,image.limits.image_bytes)
            &&c.known_image_bytes<=Math.min(p.limits.metadata_bytes,image.limits.metadata_bytes),'manifest_image');
        const sidecar={
            beforePath(role,candidate){
                c.step('records');requireValue(own(a.roles,role),'manifest_scope');
                const rel=path.relative(normalized,candidate);
                requireValue(rel!==''&&rel!=='..'&&!rel.startsWith('..'+path.sep)&&!path.isAbsolute(rel),'manifest_scope');
                requireValue(samePath(candidate,path.join(normalized,...c.role_paths[role].split('/'))),'manifest_scope');
            },
            beforeOpen(role,member){
                c.step('records');requireValue(c.role_paths[role]===member,'manifest_scope');
                c.reads.open_attempts++;requireValue(c.reads.open_attempts<=a.max_open_attempts,'manifest_scope');
            },
            openReturned(){c.reads.open_returns++;},
            openUnknown(){c.reads.open_unknown++;},
            stat(role,n){requireValue(n<=a.roles[role].max_raw_bytes,'manifest_scope');},
            readAttempt(){c.reads.read_attempts++;},
            readUnknown(){c.reads.read_unknown++;},
            readReturned(role,n,remaining){
                if(!Number.isSafeInteger(n)||n<0||n>remaining){c.reads.read_unknown++;fail('manifest_scope');}
                c.reads.read_returns++;c.reads.known_delivered_bytes+=n;
                requireValue(c.reads.known_delivered_bytes<=a.max_delivered_bytes,'manifest_scope');
            },
            closeAttempt(){c.reads.close_attempts++;},
            closeReturned(){c.reads.close_returns++;},
            closeUnknown(error,secondary){c.reads.close_unknown++;if(secondary)c.reads.secondary_cleanup_errors++;c.secondary_errors.push(error);},
            record(row){
                c.step('records');const record={...row,expected_sha256:a.roles[row.role].expected_sha256};
                const size=Object.values(record).reduce((n,v)=>n+(typeof v==='string'?2*v.length:8),0);
                c.record_primitive+=size;requireValue(c.record_primitive<=c.O,'manifest_record_budget');
                c.metadata_records.push(record);
            }
        };
        c.session=createCheckedReadSession(normalized,readPolicy,capturePolicy,image.limits,0,c.check,false,null,c,
            {manifest_sha256:c.manifest_raw_expected},sidecar);
        for(const role of basis.metadata_roles)c.role_paths[role]=c.session.relative(a.roles[role].relative_path);
        requireValue(new Set(Object.values(c.role_paths)).size===basis.metadata_roles.length,'manifest_scope');
        (c.historicalMetadata?historicalTransactions:metadataTransactions).set(c.token,{state:'PREPARING'});c.metadata_closed=false;
    }
    function acquireMetadata(root,c,p){
        const x=c.ctx,e=x.expected,a=e.scope,session=c.session,row=(c.historicalMetadata?historicalTransactions:metadataTransactions).get(c.token);
        requireValue(row&&row.state==='PREPARING','manifest_order');row.state='READING';c.stage='metadata_acquisition';
        const physical=fileOperation(()=>fs.realpathSync(root));
        requireValue(samePath(physical,c.root_normalized)&&fileOperation(()=>fs.statSync(physical)).isDirectory(),'manifest_scope');
        const read=role=>session.metadata(c.role_paths[role],role,a.roles[role].expected_sha256);
        let provenance=null,manifest=null,lock=null,pkg=null;
        try{
            provenance=read('provenance');requireValue(Array.isArray(provenance.vendored_dependencies),'manifest_metadata');
            const matches=provenance.vendored_dependencies.filter(v=>v&&v.id===e.owner_id);
            requireValue(matches.length===1,'manifest_identity');const owner=matches[0];
            requireValue(owner.locked_version===e.compiler_version&&owner.lockfile_sha256===a.roles.lockfile.expected_sha256,'manifest_identity');
            requireValue(session.relative(owner.manifest_path)===c.role_paths.manifest
                &&session.relative(owner.lockfile_path)===c.role_paths.lockfile
                &&session.relative(owner.runtime_package_path)===c.role_paths.installed_package,'manifest_scope');
            manifest=read('manifest');row.state='PAIR_VERIFIED';lock=read('lockfile');
            requireValue(manifest.dependencies?.[e.owner_id]===e.compiler_version&&typeof owner.lockfile_package_key==='string'
                &&own(lock.packages||{},owner.lockfile_package_key)&&lock.packages[owner.lockfile_package_key]?.version===e.compiler_version,'manifest_identity');
            pkg=read('installed_package');requireValue(pkg.name===e.owner_id&&pkg.version===e.compiler_version,'manifest_identity');
            row.state='METADATA_VALIDATED';c.check();
            requireValue(c.consumptions===1&&c.child_returns===1&&!(c.historicalMetadata?historicalCapsules:metadataCapsules).has(c.capsule)
                &&c.reads.open_returns===4&&c.reads.close_returns===4
                &&c.reads.open_unknown===0&&c.reads.close_unknown===0&&c.reads.read_unknown===0,'manifest_order');
            row.state='SETTLING';(c.historicalMetadata?historicalTransactions:metadataTransactions).delete(c.token);c.metadata_closed=true;c.stage='metadata_settlement';c.check();
        }finally{provenance=manifest=lock=pkg=null;c.session=null;}
    }
    function metadataResult(c,p){
        const e=c.ctx.expected,a=c.association;
        return {version:p.version,status:p.status,binding_sha256:e.binding_sha256,
            metadata_declared_consistency_checked:true,manifest_same_read_pair_observed:true,
            installed_entry_observed:false,observed_entry_sha256:null,entry_owner_allocated:false,entry_owner_disposed:null,
            entry_payload_open_attempts:0,metadata_transaction_closed:c.metadata_closed,
            metadata_records:c.metadata_records,pair:a.pair,child:a.child,
            read_accounting:{...c.reads,known_open_descriptors:0,coverage:'four_declared_metadata_payloads_only'},
            controlled_extension:{scope:'controlled_handoff_and_record_extension_only',prepaid_wrapper_work:c.wrapper_work,
                charged_wrapper_work:c.charged_work,child_work_ceiling:c.child_work,child_step_ceiling:c.child_steps,
                named_extension_upper:c.named_upper,capture_named_partial:c.capture_named_partial,known_named_partial_bytes:c.known_image_bytes,
                record_primitive_bytes:c.record_primitive,full_acquisition_work:null,actual_phase_fit:null,physical_heap_rss:null},
            lifecycle:{mints:c.mints,consume_attempts:c.consume_attempts,consumptions:c.consumptions,child_attempts:c.child_attempts,
                child_returns:c.child_returns,receipt_parse_attempts:c.receipt_parse_attempts,receipt_parse_returns:c.receipt_parse_returns,
                metadata_transaction_closed:true,references_cleared:true},
            flags:Object.fromEntries(['read_authority_granted','external_origin_authenticated','source_freshness','complete_compiler_inputs',
                'actual_owned_phase_fit','physical_memory_attested','compiler_loaded','runtime','production','semantic_lineage','cache_eligible','publication_authority'].map(k=>[k,false]))};
    }

    function checkImage(c,metrics){
        c.check();const e=c.ctx.expected.entry_expected,roleCount=Object.keys(c.ctx.profiles.private_compiler_owned_view_basis.role_expected_fields).length;
        c.known_image_bytes=metrics.known_owner_input_bytes+2*e.entry_bytes+roleCount+c.named_upper;
        const cap=Math.min(c.ctx.profiles.private_input_image_experiment.limits.image_bytes,c.ctx.profiles.private_compiler_manifest_capsule.limits.image_bytes);
        requireValue(Number.isSafeInteger(c.known_image_bytes)&&c.known_image_bytes<=cap,'manifest_image');
    }
    function observe(root,expectedInput,profilesInput,schemasInput,options,metadataOnly=false,historicalMetadata=false){
        if(active!==null){active.poisoned=true;fail('manifest_reentry');}
        const c={token:{},stage:'options',poisoned:false,mints:0,consume_attempts:0,consumptions:0,
            child_attempts:0,child_returns:0,receipt_parse_attempts:0,receipt_parse_returns:0,publications:0,owner_disposed:false,
            charged_work:0,attempted_work:0,steps:{prepare:0,receipt:0,summary:0,protocol:0,frame:0}};
        c.metadataOnly=metadataOnly;c.historicalMetadata=historicalMetadata;if(metadataOnly)c.steps.records=0;
        active=c;let owner=null,before=null,result=null,primary=null,failed=false,cleanup=[];
        try{
            requireValue(options&&typeof options==='object'&&!types.isProxy(options)
                &&[Object.prototype,null].includes(Object.getPrototypeOf(options)),'manifest_context');
            const ds=Object.getOwnPropertyDescriptors(options),ks=Reflect.ownKeys(ds);
            requireValue(ks.length===2&&['now','deadline'].every(k=>own(ds,k)&&own(ds[k],'value')),'manifest_context');
            const now=ds.now.value,deadline=ds.deadline.value;requireValue(typeof now==='function','manifest_deadline');
            c.started=now();c.last=c.started;c.deadline=deadline;
            c.check=()=>{requireValue(!c.poisoned&&active===c,'manifest_reentry');const t=now();
                requireValue(!c.poisoned&&active===c,'manifest_reentry');
                requireValue(Number.isFinite(t)&&t>=c.last&&t<deadline,'manifest_deadline');c.last=t;};
            requireValue(Number.isFinite(c.started)&&Number.isFinite(deadline)&&c.started<deadline,'manifest_deadline');c.check();
            const raw=field(profilesInput,historicalMetadata?'private_compiler_historical_metadata_observer':metadataOnly?'private_compiler_metadata_only_observer':'private_compiler_manifest_capsule'),limits=field(raw,'limits');
            c.C=field(limits,'fixed_context_bytes');c.O=field(limits,'output_bytes');
            requireValue(Number.isSafeInteger(c.C)&&c.C>0&&Number.isSafeInteger(c.O)&&c.O>0,'manifest_context');
            c.step=(group,n=1)=>{requireValue(Number.isSafeInteger(n)&&n>=0,'manifest_steps');
                c.steps[group]+=n;requireValue(c.steps[group]<=(group==='frame'?171:group==='protocol'||group==='records'?c.O:4*c.O),'manifest_steps');c.check();};
            c.charged_work=2*c.C+6*c.O;c.attempted_work=c.charged_work;c.stage='prepare';
            c.ctx=capture({expected:expectedInput,profiles:profilesInput,schemas:schemasInput},c,c.C);
            const x=c.ctx,p=x.profiles[historicalMetadata?'private_compiler_historical_metadata_observer':metadataOnly?'private_compiler_metadata_only_observer':'private_compiler_manifest_capsule'],s=x.schemas[metadataOnly?'metadata_observer':'manifest_capsule'];
            if(metadataOnly){
                keys(x.expected,['binding_sha256','compiler_version','invocation_id','joint_expected','owner_id','scope']);
                keys(x.schemas,['joint','metadata_observer']);
                keys(x.profiles,[historicalMetadata?'private_compiler_historical_metadata_observer':'private_compiler_metadata_only_observer','private_compiler_entry_owner','compiler_library_inputs',
                    'private_input_image_experiment','private_compiler_metadata_joint','private_compiler_owned_view_basis','private_compiler_owned_view_plan']);
                fixedValidate(p,s.$defs.metadata_policy,s,c,'prepare');
                if(historicalMetadata){
                    requireValue(p.version==='v1-private-historical-metadata-observer'&&p.model==='historical_anchor_four_metadata_fixed_pair_v1'
                        &&p.scope==='historical_anchor_four_metadata_manifest_pair_only'
                        &&p.status==='HISTORICAL_FOUR_METADATA_COMPARISON_NOT_SOURCE_ATTESTATION'
                        &&p.schema==='config/schemas/compiler_metadata_actual_observer.schema.json'
                        &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>p[k]===false),'manifest_context');
                }else{
                requireValue(p.version==='v1-private-four-metadata-observer'&&p.model==='temporary_synthetic_four_metadata_fixed_pair_v1'
                    &&p.scope==='temporary_synthetic_four_metadata_manifest_pair_only'
                    &&p.status==='FOUR_METADATA_MANIFEST_PAIR_OBSERVED_NOT_ENTRY_ADMISSION'
                    &&p.schema==='config/schemas/compiler_metadata_observer.schema.json'
                    &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>p[k]===false),'manifest_context');
                }
            }else{
            keys(x.expected,['binding_sha256','entry_context_sha256','entry_expected','joint_expected']);
            keys(x.schemas,['entry','joint','manifest_capsule']);
            keys(x.profiles,['private_compiler_manifest_capsule','private_compiler_entry_owner','compiler_library_inputs','private_input_image_experiment',
                'private_compiler_metadata_joint','private_compiler_owned_view_basis','private_compiler_owned_view_plan']);
            fixedValidate(p,s.$defs.manifest_policy,s,c,'prepare');
            requireValue(p.version==='v1-private-manifest-same-read-capsule'&&p.model==='temporary_synthetic_manifest_fixed_consumer_v1'
                &&p.scope==='temporary_synthetic_manifest_same_read_pair_only'&&p.status==='SYNTHETIC_MANIFEST_SAME_READ_PAIR_OBSERVED_NOT_OWNER_ADMISSION'
                &&p.schema==='config/schemas/compiler_manifest_capsule.schema.json'
                &&['default_enabled','native_execution_enabled','production_enabled','semantic_lineage','cache_eligible'].every(k=>p[k]===false),'manifest_context');
            }
            const child=x.profiles.private_compiler_metadata_joint,b=child.sublimits,l=child.limits;
            const basis=x.profiles.private_compiler_owned_view_basis,planner=x.profiles.private_compiler_owned_view_plan;
            requireValue(basis.role_expected_fields.manifest==='manifest_sha256'&&basis.metadata_roles.includes('manifest')
                &&basis.entry_role==='installed_entry'&&Object.keys(basis.role_expected_fields).length===5,'manifest_context');
            const W=p.limits.metadata_byte_work_units,P=p.limits.primitive_steps;
            requireValue(p.limits.fixed_context_bytes===b.fixed_context_bytes&&p.limits.output_bytes===l.output_bytes
                &&Object.entries(p.limits).every(([k,v])=>Number.isSafeInteger(v)&&v>0
                    &&(!own(basis.limits,k)||(v<=basis.limits[k]&&v<=planner.limits[k]))),'manifest_context');
            requireValue(deadline<=c.started+p.limits.operation_ms,'manifest_deadline');
            const n=Math.min(b.node_count,1+b.property_count+b.array_edge_count),pr=Math.min(b.property_count,n-1);
            c.R=64+16*n+16*pr+4*(n-1-pr);const F=12*(b.max_depth+1),T=171,Q=b.payload_bytes;
            requireValue([c.R,F,Q].every(v=>Number.isSafeInteger(v)&&v>0),'manifest_context');
            c.wrapper_work=2*c.C+(metadataOnly?38:34)*c.O+Q+c.R+3*T;c.child_work=W-c.wrapper_work;
            c.wrapper_steps=(metadataOnly?14:13)*c.O+T;c.child_steps=P-c.wrapper_steps;
            requireValue(c.child_work>0&&c.child_steps>0&&l.metadata_byte_work_units===c.child_work
                &&b.primitive_steps===c.child_steps,'manifest_budget');
            const base=2*b.snapshot_ceiling+8*b.property_count+3*b.scalar_bytes+2*Q+4*c.O+10*c.C+2*c.R+F+64+T;
            c.named_upper=base+Math.min(b.snapshot_ceiling,3*b.utf16_units)+6*b.utf16_units+2*b.property_count+2*c.C+(metadataOnly?32:30)*c.O+T;
            requireValue(c.named_upper<=p.limits.metadata_bytes&&c.named_upper<=p.limits.image_bytes,'manifest_budget');
            let seed;
            if(metadataOnly){prepareMetadata(root,c,p,basis);seed=metadataSeed(c,p,s);}
            else{c.manifest_raw_expected=x.expected.entry_expected.manifest_sha256;
            seed={child_policy_sha256:x.expected.joint_expected.policy_sha256,entry_context_sha256:x.expected.entry_context_sha256,
                intent_sha256:x.expected.joint_expected.intent_sha256,manifest_raw_sha256:x.expected.entry_expected.manifest_sha256,
                policy:p,policy_schema:s,schema_sha256:x.expected.joint_expected.schema_sha256};
            }
            const wire=joint._renderOrderedAsciiContext(seed,c.O,{add:(...v)=>v.reduce((a,b)=>a+b,0),mul:(a,b)=>a*b,
                need:requireValue,step:n=>c.step('prepare',n),check:c.check,tightAscii:true},{context_encode:0});
            requireValue(hashBytes(wire)===x.expected.binding_sha256&&(!metadataOnly||x.expected.joint_expected.intent_sha256===x.expected.binding_sha256),'manifest_identity');c.check();
            c.stage='acquisition';
            if(metadataOnly)acquireMetadata(root,c,p);
            else{
            const admitted={private_compiler_entry_owner:x.profiles.private_compiler_entry_owner,
                compiler_library_inputs:x.profiles.compiler_library_inputs,private_input_image_experiment:x.profiles.private_input_image_experiment};
            owner=createEntrySourceOwner(root,x.expected.entry_expected,admitted,x.schemas.entry,
                {now:()=>{c.check();return c.last;},deadline},false,null,null,c);
            c.stage='owner_settlement';c.check();
            requireValue(owners.has(owner)&&c.consumptions===1&&c.child_returns===1&&!capsules.has(c.capsule),'manifest_order');
            before=owner.snapshot();
            requireValue(before.state==='READY'&&before.entry_owner_registered&&before.entry_input_verified
                &&before.entry_sha256===x.expected.entry_expected.entry_sha256&&before.context_sha256===x.expected.entry_context_sha256,'manifest_order');
            const dispose=owners.get(owner).dispose;dispose();const after=owner.snapshot();
            requireValue(after.state==='DISPOSED'&&!owners.has(owner)&&after.retained_entry_bytes===0&&after.retained_text_code_units===0
                &&!after.entry_owner_registered&&!after.entry_input_verified,'manifest_cleanup');c.owner_disposed=true;c.check();
            }
            c.stage='summary';const a=c.association;
            result=metadataOnly?metadataResult(c,p):{version:p.version,status:p.status,binding_sha256:x.expected.binding_sha256,raw_manifest:a.raw_manifest,pair:a.pair,child:a.child,
                owner:{context_sha256:before.context_sha256,entry_sha256:before.entry_sha256,capture_read_bytes:before.capture_read_bytes,
                    known_owner_input_bytes:before.known_owner_input_bytes,disposed:true,registered:false,retained_entry_bytes:0,retained_text_code_units:0},
                controlled_extension:{scope:'controlled_handoff_extension_only',prepaid_wrapper_work:c.wrapper_work,
                    charged_wrapper_work:c.charged_work,child_work_ceiling:c.child_work,child_step_ceiling:c.child_steps,
                    named_extension_upper:c.named_upper,known_image_bytes:c.known_image_bytes,full_acquisition_work:null,actual_phase_fit:null,physical_heap_rss:null},
                lifecycle:{mints:c.mints,consume_attempts:c.consume_attempts,consumptions:c.consumptions,child_attempts:c.child_attempts,child_returns:c.child_returns,
                    receipt_parse_attempts:c.receipt_parse_attempts,receipt_parse_returns:c.receipt_parse_returns,owner_disposed:true,references_cleared:true},
                flags:Object.fromEntries(['read_authority_granted','external_origin_authenticated','source_freshness','actual_owned_phase_fit',
                    'physical_memory_attested','compiler_loaded','runtime','production','semantic_lineage','cache_eligible','publication_authority'].map(k=>[k,false]))};
            // Bound ASCII output before native serialization; no second canonical encoder.
            joint._renderOrderedAsciiContext(result,c.O,{add:(...v)=>v.reduce((a,b)=>a+b,0),mul:(a,b)=>a*b,
                need:requireValue,step:n=>c.step('summary',n),check:c.check,tightAscii:true,insertionOrder:true},{context_encode:0});c.check();
            const freeze=v=>{c.step('summary');if(v&&typeof v==='object'){for(const z of Object.values(v))freeze(z);Object.freeze(v);}};
            freeze(result);c.check();
        }catch(e){primary=e;failed=true;result=null;}
        finally{
            try{disposeCapsule(c.capsule);}catch(e){cleanup.push(e);}
            try{owners.get(owner)?.dispose();}catch(e){cleanup.push(e);}
            if(metadataOnly){(c.historicalMetadata?historicalTransactions:metadataTransactions).delete(c.token);c.metadata_closed=true;}
            c.ctx=c.association=c.capsule=c.token=c.metadata_records=c.session=null;owner=before=null;
            if(cleanup.length){primary=new AggregateError(failed?[primary,...cleanup]:cleanup,'manifest_cleanup_failed',failed?{cause:primary}:undefined);failed=true;result=null;}
            if(!failed){try{c.check();}catch(e){primary=e;failed=true;result=null;}}
            if(active===c)active=null;
        }
        if(failed){
            if(object(primary)&&c.secondary_errors?.length)secondaryFailures.set(primary,Object.freeze([...c.secondary_errors]));
            if(object(primary))failures.set(primary,{stage:c.stage,code:primary instanceof ManifestCapsuleRefusal?primary.code:'manifest_component_or_unexpected',
                mints:c.mints,consume_attempts:c.consume_attempts,consumptions:c.consumptions,child_attempts:c.child_attempts,child_returns:c.child_returns,
                receipt_parse_attempts:c.receipt_parse_attempts,receipt_parse_returns:c.receipt_parse_returns,publications:0,owner_disposed:c.owner_disposed,
                prepaid_wrapper_work:c.wrapper_work??null,charged_wrapper_work:c.charged_work,attempted_wrapper_work:c.attempted_work,
                metadata_only:metadataOnly,read_accounting:metadataOnly?{...c.reads,known_open_descriptors:c.reads&&!(c.reads.open_unknown||c.reads.close_unknown)?c.reads.open_returns-c.reads.close_returns:null}:null,metadata_transaction_closed:metadataOnly?c.metadata_closed:null,
                secondary_native_failures_retained:metadataOnly?secondaryFailures.has(primary):null,
                steps:{...c.steps},cleanup_errors:cleanup.length,result_published:false,references_cleared:true,full_acquisition_work:null,
                full_capture_failure_work:null,physical_heap_rss:null,new_deadline:false});
            throw primary;
        }
        c.publications++;return result;
    }
    return {stage,checkImage,observe,failureObservation:error=>object(error)&&failures.has(error)?structuredClone(failures.get(error)):null};
})();
function observeSyntheticManifestPairCapture(root,expected,profiles,schemas,options){return manifestCapsule.observe(root,expected,profiles,schemas,options);}
function observeSyntheticCompilerMetadataPair(root,expected,profiles,schemas,options){return manifestCapsule.observe(root,expected,profiles,schemas,options,true);}

function observeHistoricalCompilerMetadataPair(root,expected,profiles,schemas,options){return manifestCapsule.observe(root,expected,profiles,schemas,options,true,true);}

function observeHistoricalCompilerEntryUse(root,expected,profiles,schemas,options){
    return entryIdentity.useActual(root,expected,profiles,schemas,options);
}
const lifetimeTrace=require('./compiler_entry_lifetime_trace.cjs')._bindEntryLifetimeTrace(createEntrySourceOwner,_borrowCompilerEntryText,_disposeCompilerEntry,CompilerEntryRefusal);
function observeSyntheticEntryOwnerLifetime(root,expected,profiles,schemas,options){return lifetimeTrace.observe(root,expected,profiles,schemas,options);}
module.exports={observeHistoricalCompilerEntryUse,observeSyntheticCompilerEntryUse,compilerEntryUseFailureObservation:entryIdentity.useFailure,observeSyntheticCompilerEntryIdentity,compilerEntryIdentityFailureObservation:entryIdentity.failure,observeHistoricalCompilerMetadataPair,observeSyntheticCompilerMetadataPair,observeSyntheticManifestPairCapture,ManifestCapsuleRefusal,manifestCapsuleFailureObservation:manifestCapsule.failureObservation,
    observeSyntheticEntryOwnerLifetime,compilerEntryLifetimeTraceFailureObservation:lifetimeTrace.failure,
    _consumeCompilerOwnedRowFragmentCapsule:rowConsumer.consumeCapsule,_disposeCompilerOwnedRowFragmentCapsule:rowConsumer.disposeCapsule,
    createCompilerOwnedRowFragmentOwner,observeCompilerOwnedRowFragment,
    _consumeCompilerTextWindowCapsule:textConsumer.consumeCapsule,_disposeCompilerTextWindowCapsule:textConsumer.disposeCapsule,createCompilerTextWindowOwner,observeCompilerTextWindowCandidate,createCompilerEntryOwner,_borrowCompilerEntryText,_disposeCompilerEntry,CompilerEntryRefusal};

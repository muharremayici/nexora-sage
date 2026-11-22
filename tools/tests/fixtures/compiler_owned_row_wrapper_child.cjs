'use strict';
// Trusted first-party, one-shot text-only child. Metadata is linkage, not permission authentication.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),Module=require('node:module'),vm=require('node:vm');
const {performance}=require('node:perf_hooks'),started=performance.now();
const source=require('../../core/compiler_entry_owner.cjs'),kernel=require('../../core/compiler_owned_row_fragment.cjs');
const {_canonicalCompilerStreamMetadata:canonical}=require('../../core/compiler_stream_stager.cjs');
const stdout=process.stdout,rawRequest=fs.readFileSync(0),request=JSON.parse(rawRequest.toString('utf8'));
const sha=v=>crypto.createHash('sha256').update(v).digest('hex'),hash=v=>sha(canonical(v)),hex=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const native={},fds=new Map(),roles=new Map(),reads=[],backends={},nativeLoad=Module._load;
for(const k of ['realpathSync','statSync','openSync','fstatSync','readSync','closeSync','readFileSync','readFile','open','readdirSync','readdir','writeFileSync','writeFile','appendFileSync'])native[k]=fs[k];
let attempts=0,opens=0,closes=0,delivered=0,unscoped=0,backendCalls=0,lateLoads=0,owner,stage='preflight',installed=false;
const out={status:'OWNED_ROW_OBSERVATION_NOT_COMPLETED',controller_result_sha256:request.controller_result_sha256,node_version:process.version};
const fail=code=>{const e=new Error(code);e.code=code;throw e;},need=(v,c)=>{if(!v)fail(c);};
const normalize=v=>process.platform==='win32'?path.resolve(v).toLowerCase():path.resolve(v);
const expectedRoles={provenance:'config/third_party_distribution_contract.json',manifest:'tools/engines/package.json',lockfile:'tools/engines/package-lock.json',
    installed_package:'tools/engines/node_modules/typescript/package.json',installed_entry:'tools/engines/node_modules/typescript/lib/typescript.js'};
let deadline=null;
try{
    const e=request.expected,s=request.read_scope,image=request.profiles.private_input_image_experiment;
    need(s?.granted===true&&s.purpose==='observe_owned_local_row_fragment','fresh_scope_required');
    need(typeof s.decision_reference==='string'&&s.decision_reference.length>0&&Buffer.byteLength(s.decision_reference,'utf8')<=256,'fresh_scope_required');
    need(typeof s.new_invocation_id==='string'&&/^owned-row-[A-Za-z0-9-]+$/.test(s.new_invocation_id)&&Buffer.byteLength(s.new_invocation_id,'utf8')<=64
        &&s.new_invocation_id===e.invocation_id&&e.invocation_id===e.base_request.entry_expected.invocation_id&&e.invocation_id===e.fragment_expected.invocation_id,'fresh_invocation_required');
    need(sha(s.decision_reference)===e.integration_authority_ref_sha256&&sha(s.decision_reference)===e.base_request.authority.authority_ref_sha256,'scope_reference_mismatch');
    need(hex(request.controller_result_sha256)&&request.controller_request_sha256===e.base_request_sha256&&hash(e.base_request)===e.base_request_sha256,'controller_request_mismatch');
    need(Number.isSafeInteger(s.max_child_request_utf8_bytes)&&s.max_child_request_utf8_bytes>0&&s.max_child_request_utf8_bytes<=image.limits.metadata_bytes
        &&rawRequest.length<=s.max_child_request_utf8_bytes,'request_metadata_budget');
    need(Number.isSafeInteger(s.api_known_inputs_bound)&&s.api_known_inputs_bound>0&&Number.isSafeInteger(5*s.max_child_request_utf8_bytes+s.api_known_inputs_bound)
        &&5*s.max_child_request_utf8_bytes+s.api_known_inputs_bound<=image.limits.image_bytes,'joined_wire_budget');
    need(Number.isSafeInteger(request.operation_ms)&&request.operation_ms>0&&request.operation_ms<=request.profiles.private_compiler_owned_row_fragment.limits.operation_ms
        &&request.operation_ms<=30000,'operation_deadline');deadline=started+request.operation_ms;
    const root=path.resolve(request.root);need(path.isAbsolute(request.root)&&sha(normalize(root))===e.base_request.authority.root_sha256,'declared_node_root_mismatch');
    need(Array.isArray(request.file_roles)&&request.file_roles.length===5&&Array.isArray(s.paths)&&s.paths.length===5,'wrong_read_scope');
    const seen=new Set();let expectedBytes=0;
    for(const row of request.file_roles){
        need(Object.hasOwn(expectedRoles,row.role)&&row.relative_path===expectedRoles[row.role]&&!seen.has(row.role),'wrong_payload_role');seen.add(row.role);
        need(hex(row.expected_sha256)&&Number.isSafeInteger(row.historical_size_bytes)&&row.historical_size_bytes>0,'wrong_payload_role');
        const target={installed_package:'package',installed_entry:'entry'}[row.role]||row.role;
        need(row.expected_sha256===e.base_request.entry_expected[target+'_sha256'],'payload_identity_mismatch');
        expectedBytes+=row.historical_size_bytes;roles.set(normalize(path.join(root,...row.relative_path.split('/'))),row.role);
    }
    need(JSON.stringify([...s.paths].sort())===JSON.stringify(Object.values(expectedRoles).sort()),'wrong_read_scope');
    need(s.max_payload_open_attempts===5&&s.owner_api_max_entry_bytes===e.base_request.entry_expected.entry_bytes
        &&s.owner_api_max_entry_bytes===request.file_roles.find(r=>r.role==='installed_entry').historical_size_bytes
        &&s.owner_api_max_total_bytes===expectedBytes&&s.owner_api_max_entry_bytes===e.base_request.authority.max_entry_bytes
        &&s.owner_api_max_total_bytes===e.base_request.authority.max_total_bytes
        &&s.max_delivered_entry_bytes_including_eof_probe===s.owner_api_max_entry_bytes+1
        &&s.max_delivered_total_bytes_including_five_eof_probes===expectedBytes+5
        &&s.metadata_file_stat_cap===request.profiles.compiler_library_inputs.max_file_bytes
        &&s.metadata_file_delivered_cap_including_eof_probe===s.metadata_file_stat_cap+1,'wrong_read_budget');
    need(hash(s.selection)===hash(e.base_request.selection),'selection_scope_mismatch');
    const directories=new Set([normalize(root),normalize(path.join(root,'tools/engines/node_modules/typescript'))]);
    const allowed=value=>{const full=normalize(value);if(!roles.has(full)&&!directories.has(full)){unscoped++;fail('unscoped_payload_path');}return full;};
    fs.realpathSync=value=>{allowed(value);const resolved=native.realpathSync(value);allowed(resolved);return resolved;};
    fs.statSync=value=>{allowed(value);return native.statSync(value);};
    fs.openSync=(value,mode,...rest)=>{
        const full=allowed(value),role=roles.get(full);attempts++;
        if(!role||mode!=='r'||attempts>s.max_payload_open_attempts)fail('payload_open_scope');
        const fd=native.openSync(value,mode,...rest);opens++;const row={role,delivered_bytes:0,read_calls:0,closed:false};reads.push(row);fds.set(fd,row);return fd;
    };
    fs.fstatSync=fd=>{if(!fds.has(fd))fail('unowned_payload_descriptor');return native.fstatSync(fd);};
    fs.readSync=(fd,buffer,offset,length,position)=>{
        const row=fds.get(fd);if(!row)fail('unowned_payload_descriptor');
        const cap=row.role==='installed_entry'?s.max_delivered_entry_bytes_including_eof_probe:s.metadata_file_delivered_cap_including_eof_probe;
        if(!Number.isSafeInteger(length)||length<0||length>cap-row.delivered_bytes||length>s.max_delivered_total_bytes_including_five_eof_probes-delivered)fail('delivered_payload_budget');
        const n=native.readSync(fd,buffer,offset,length,position);row.read_calls++;row.delivered_bytes+=n;delivered+=n;return n;
    };
    fs.closeSync=fd=>{const row=fds.get(fd);if(!row)fail('unowned_payload_descriptor');native.closeSync(fd);row.closed=true;fds.delete(fd);closes++;};
    for(const k of ['readFileSync','readFile','open','readdirSync','readdir','writeFileSync','writeFile','appendFileSync'])fs[k]=()=>{unscoped++;fail('unowned_payload_io');};
    Module._load=()=>{lateLoads++;fail('late_module_load');};
    for(const k of ['compileFunction','createContext','runInContext','runInNewContext','runInThisContext','Script']){backends[k]=vm[k];vm[k]=()=>{backendCalls++;fail('input_backend_forbidden');};}
    installed=true;const options={now:()=>performance.now(),deadline};
    stage='construction';owner=source.createCompilerOwnedRowFragmentOwner(root,e,request.profiles,request.schemas,options);out.ready=owner.snapshot();
    stage='observation';out.result=source.observeCompilerOwnedRowFragment(owner,e,request.profiles,request.schemas,options);out.after=owner.snapshot();
    need(out.result.known_inputs_reserved_bytes<=s.api_known_inputs_bound,'declared_api_bound_exceeded');
    out.status=out.result.fragment_result.observation.fragment_syntax_complete?'OWNED_ROW_OBSERVATION_COMPLETED_WITH_COMPLETE_LOCAL_SYNTAX':'OWNED_ROW_OBSERVATION_COMPLETED_WITH_INCOMPLETE_SYNTAX';
}catch(error){
    out.status='OWNED_ROW_OBSERVATION_REFUSED_OR_FAILED';delete out.result;
    out.error={type:error?.name||'unknown',code:error?.code||null,stage:kernel.failureStage(error)==='unknown'?stage:kernel.failureStage(error)};
}finally{
    try{if(owner){if(owner.snapshot().state!=='DISPOSED')owner.dispose();out.final_owner=owner.snapshot();}}
    catch(error){out.cleanup_error={type:error?.name||'unknown',code:error?.code||null};out.status='OWNED_ROW_OBSERVATION_REFUSED_OR_FAILED';delete out.result;}
    for(const [fd,row]of fds){try{native.closeSync(fd);row.closed=true;closes++;}catch(error){out.cleanup_error={type:error?.name||'unknown',code:error?.code||null};}out.emergency_descriptor_cleanup=true;}
    fds.clear();if(installed){for(const [k,v]of Object.entries(native))fs[k]=v;Module._load=nativeLoad;for(const [k,v]of Object.entries(backends))vm[k]=v;}
}
out.payload_accounting={open_attempts:attempts,successful_opens:opens,closes,delivered_bytes:delivered,open_descriptors:fds.size,roles:reads,
    coverage:'five_declared_payload_roles_only',support_module_and_other_workflow_io:'not_instrumented'};
out.unscoped_access_attempts=unscoped;out.late_module_load_attempts=lateLoads;out.blocked_backend_attempts=backendCalls;
out.raw_source_persisted=false;out.python_recapture_invocations=0;out.child_request_utf8_bytes=rawRequest.length;out.wire_logical_text_reservation_bytes=5*rawRequest.length;
out.elapsed_operation_ms=performance.now()-started;
if((deadline!==null&&performance.now()>=deadline)||out.emergency_descriptor_cleanup||out.cleanup_error){
    out.status='OWNED_ROW_OBSERVATION_REFUSED_OR_FAILED';delete out.result;out.error??={type:'ScopeSettlementError',code:'observation_final_settlement',stage:'settlement'};
}
let serialized=JSON.stringify(out)+'\n';
if(Buffer.byteLength(serialized,'utf8')>request.profiles.private_input_image_experiment.limits.metadata_bytes)throw Error('result_metadata_budget');
if(deadline!==null&&performance.now()>=deadline&&out.result){out.status='OWNED_ROW_OBSERVATION_REFUSED_OR_FAILED';delete out.result;out.error={type:'ScopeSettlementError',code:'observation_final_serialization',stage:'settlement'};serialized=JSON.stringify(out)+'\n';}
stdout.write(serialized);

'use strict';
// Fixed authored-fixture transaction. No installed loader or selectable consumer.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {performance}=require('node:perf_hooks');
const entry=require('./compiler_entry_owner.cjs'),bridge=require('./loaded_fixture_image.cjs');
const declaration=require('./compiler_declaration_text.cjs');
const { _canonicalCompilerStreamMetadata:canonical,_validateCompilerStreamValue:validate}=require('./compiler_stream_stager.cjs');
const CANON=require('../../config/schemas/fixture_image_process.schema.json');
const sha=v=>crypto.createHash('sha256').update(v).digest('hex'),hash=v=>sha(canonical(v));
const bytes=v=>Buffer.byteLength(canonical(v),'utf8');
const need=v=>{if(!v)throw Error('fixture process refused');};
const same=(a,b)=>canonical(a)===canonical(b);
const equalPath=(a,b)=>process.platform==='win32'?a.toLowerCase()===b.toLowerCase():a===b;

function loadSchemas(){
    const schemas={};
    for(const [name,row]of Object.entries(CANON.$defs.policy.properties.schema_sources.const)){
        const member=path.resolve(__dirname,'../../',row.path);let fd,raw=null,text=null;
        try{
            fd=fs.openSync(member,'r');need(fs.fstatSync(fd).size===row.raw_bytes);
            raw=Buffer.alloc(row.raw_bytes+1);let n=0;
            while(n<raw.length){const got=fs.readSync(fd,raw,n,raw.length-n,null);if(got===0)break;n+=got;}
            need(n===row.raw_bytes&&sha(raw.subarray(0,n))===row.raw_sha256);
            text=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(raw.subarray(0,n));
            schemas[name]=JSON.parse(text);need(hash(schemas[name])===row.canonical_sha256);
        }finally{if(fd!==undefined)fs.closeSync(fd);raw=text=null;}
    }
    return schemas;
}

function expand(packed,schemas){
    const {profiles}=packed.support;
    need(same(packed.support.schemas,Object.fromEntries(Object.entries(schemas).map(([k,v])=>[k,hash(v)]))));
    const evaluation=['private_loaded_fixture_image','private_compiler_entry_owner','compiler_library_inputs','private_input_image_experiment',
        'private_prepared_compiler_image_ingress','private_compiler_declaration_text_handoff_preparation'];
    const stream=['private_compiler_stream_layout','private_compiler_stream_stager','private_compiler_stream_js_stager','private_owned_config_handoff',
        'private_owned_text_bridge','private_input_image_experiment','compiler_library_inputs','private_compiler_declaration_text_handoff_preparation'];
    const image=packed.image;
    return {fixture:{...packed.fixture,profiles:Object.fromEntries(evaluation.map(k=>[k,profiles[k]])),schemas:{entry:schemas.entry,fixture:schemas.fixture}},
        image:{expected:image.expected,entries:image.entries,context:{binding:image.binding,source_context:image.source_context,
            policy:profiles.private_input_image_experiment,admission:{policy:profiles.private_prepared_compiler_image_ingress,schema:schemas.ingress,
                expected:image.admission_expected,compiler_manifest:packed.stream.manifest}}},
        stream:{...packed.stream,profiles:Object.fromEntries(stream.map(k=>[k,profiles[k]])),schema:schemas.stream,preparation_schema:schemas.preparation}};
}

function imageUpper(c){
    const ctx=c.image.context,root=ctx.admission.policy.compiler_virtual_root;
    const rows=c.image.entries.map(r=>[r.path,r.bytes]);
    for(const r of c.stream.manifest.files)rows.push([root+'/'+r.name,r.bytes]);
    let result=bytes(ctx.binding)+bytes(ctx.policy)+bytes({binding:ctx.binding,image_policy:ctx.policy,admission:ctx.admission});
    for(const [p,n]of rows){
        need(typeof p==='string'&&p.startsWith('/')&&p===path.posix.normalize(p)&&!p.endsWith('/'));
        result+=bytes({path:p,role:'auxiliary',size_bytes:n,raw_sha256:'f'.repeat(64),parser_sha256:'f'.repeat(64),host_sha256:'f'.repeat(64)});
        let current=path.posix.dirname(p),child=null;
        for(;;){
            result+=bytes({path:current,files:child===null?[path.posix.basename(p)]:[],directories:child===null?[]:[path.posix.basename(child)]});
            if(current==='/')break;
            child=current;current=path.posix.dirname(current);
        }
    }
    return result;
}

function reservation(c,p,s,packed,schemas){
    const manifest=c.stream.manifest,source=c.image.entries.reduce((n,r)=>n+r.bytes,0);
    const declared=manifest.files.reduce((n,r)=>n+r.bytes,0),m=p.named_forms;
    const input=bytes(c),context=bytes(packed)+bytes(p)+bytes(s);
    // Parent uses the canonical stream geometry owner; its independently bound
    // exact largest frame is checked per actual received feed below.
    const inventory=imageUpper(c),raw=Object.values(c.fixture.role_bytes).reduce((a,b)=>a+b,0)+Object.keys(c.fixture.role_bytes).length;
    const schemaBytes=Object.values(schemas).reduce((n,v)=>n+bytes(v),0);
    const rawMax=Math.max(...Object.values(p.schema_sources).map(v=>v.raw_bytes+1));
    return {input,context,inventory,raw,source,declared,m,schemaBytes,rawMax};
}

function installGuard(f,p,check){
    const native=Object.fromEntries(['realpathSync','statSync','openSync','fstatSync','readSync','closeSync'].map(k=>[k,fs[k]]));
    const boundary=path.resolve(f.boundary),root=path.join(boundary,p.root_member);
    need(equalPath(boundary,f.boundary)&&equalPath(root,f.root)&&path.basename(boundary).startsWith(p.boundary_prefix));
    const relative=path.relative(path.resolve(os.tmpdir()),boundary);
    need(relative!==''&&relative!=='..'&&!relative.startsWith('..'+path.sep)&&!path.isAbsolute(relative));
    // Canonicality checks occur only after the complete closed bootstrap is admitted.
    need(equalPath(native.realpathSync(boundary),boundary)&&equalPath(native.realpathSync(root),root));
    const rows=new Map(Object.entries(p.role_paths).map(([role,member])=>[path.join(root,...member.split('/')),{role,cap:f.role_bytes[role]}]));
    const fds=new Map(),opened=new Set(),counts={opens:0,closes:0,entry_opens:0,forbidden:0};
    const inside=value=>{check();need(typeof value==='string');const full=path.resolve(value),r=path.relative(root,full);
        if(r==='..'||r.startsWith('..'+path.sep)||path.isAbsolute(r)){counts.forbidden++;throw Error('fixture scope');}return full;};
    fs.realpathSync=value=>{const full=inside(value),real=native.realpathSync(value);need(equalPath(full,real));return real;};
    fs.statSync=value=>{inside(value);return native.statSync(value);};
    fs.openSync=(value,flags)=>{
        const full=inside(value),row=rows.get(full);need(row&&flags==='r'&&!opened.has(full));
        const fd=native.openSync(value,flags);opened.add(full);fds.set(fd,{...row,delivered:0});
        counts.opens++;if(row.role==='entry')counts.entry_opens++;return fd;
    };
    fs.fstatSync=fd=>{check();const row=fds.get(fd);need(row);const s=native.fstatSync(fd);need(s.isFile()&&s.size===row.cap);return s;};
    fs.readSync=(fd,buffer,offset,length,position)=>{
        check();const row=fds.get(fd);need(row&&position===null&&length<=row.cap+1-row.delivered);
        const n=native.readSync(fd,buffer,offset,length,position);row.delivered+=n;need(row.delivered<=row.cap);return n;
    };
    fs.closeSync=fd=>{need(fds.has(fd));native.closeSync(fd);fds.delete(fd);counts.closes++;};
    for(const key of ['readFileSync','readFile','open','readdirSync','readdir','writeFileSync','writeFile','writeSync']){
        fs[key]=()=>{counts.forbidden++;throw Error('fixture IO refused');};
    }
    return {counts,settled:()=>fds.size===0,
        cleanup(){let clean=true;for(const fd of fds.keys()){try{native.closeSync(fd);fds.delete(fd);}catch{clean=false;}}
            return clean&&fds.size===0;}};
}

async function run(){
    const cap=Number(process.argv[2]),allowance=Number(process.argv[3]),started=performance.now();
    need(process.argv.length===4&&cap===CANON.$defs.policy.properties.metadata_bytes.const
        &&Number.isSafeInteger(allowance)&&allowance>0&&allowance<=CANON.$defs.policy.properties.operation_ms.const);
    let bootstrap=null,prefix=Buffer.alloc(0),gate=null,owner=null,loaded=null,prepared=null,guard=null;
    let deadline=started+allowance,timer=setTimeout(()=>process.exit(1),allowance),failed=null,stage='bootstrap';
    const check=()=>need(performance.now()<deadline);
    let result=null,finalAck=null,held=null,cleanupSettled=true,schemas=null;
    const send=async value=>{check();const raw=Buffer.from(JSON.stringify(value)+'\n');
        need(raw.length<=(held?.profile.output_bytes||cap));
        await new Promise((resolve,reject)=>process.stdout.write(raw,error=>error?reject(error):resolve()));check();};
    try{
        stage='support';schemas=loadSchemas();stage='bootstrap';
        for await(const chunk of process.stdin){
            check();
            if(!bootstrap){
                need(prefix.length+chunk.length<=cap);prefix=Buffer.concat([prefix,chunk]);
                const newline=prefix.indexOf(10);if(newline<0)continue;
                need(newline===prefix.length-1);
                const text=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(prefix.subarray(0,-1));
                held=JSON.parse(text);need(text===canonical(held));prefix=null;
                need(same(Object.keys(held).sort(),['context','context_sha256','kind','profile','remaining_ms','reservation','schema'].sort()));
                const {context:packed,profile:p,schema:s,remaining_ms:remaining,reservation:r}=held;
                need(same(s,CANON));
                stage='policy';validate(p,s.$defs.policy,s,'fixture_process_policy');validate(packed,s.$defs.context,s,'fixture_process_context');
                const c=expand(packed,schemas);held.expanded=c;
                need(held.kind==='bootstrap'&&p.version==='v1-private-fixture-image-process'&&p.schema_sha256===hash(s)
                    &&hash(packed)===held.context_sha256&&cap===p.metadata_bytes&&allowance<=p.operation_ms
                    &&Number.isSafeInteger(remaining)&&remaining>0&&remaining<=allowance);
                deadline=Math.min(started+allowance,performance.now()+remaining);clearTimeout(timer);
                timer=setTimeout(()=>process.exit(1),Math.max(0,deadline-performance.now()));check();
                stage='context';const f=c.fixture,ep=f.profiles,fixture=ep.private_loaded_fixture_image;
                need(p.fixture_policy_sha256===hash(fixture)&&p.entry_policy_sha256===hash(ep.private_compiler_entry_owner)
                    &&hash(f.schemas.fixture)===fixture.schema_sha256&&hash(f.schemas.entry)===ep.private_compiler_entry_owner.schema_sha256);
                validate(fixture,f.schemas.fixture.$defs.policy,f.schemas.fixture,'fixture_policy');
                validate(f.expected,f.schemas.fixture.$defs.expected,f.schemas.fixture,'fixture_expected');
                validate(f.owner_expected,f.schemas.entry.$defs.expected,f.schemas.entry,'fixture_entry');
                need(f.owner_expected.compiler_version===fixture.fixture_version&&f.owner_expected.entry_sha256===fixture.fixture_source_sha256
                    &&f.role_bytes.entry===f.owner_expected.entry_bytes&&same(Object.keys(f.role_bytes).sort(),Object.keys(p.role_paths).sort())
                    &&f.expected.entry.context_sha256===hash(f.owner_expected)&&f.expected.policy_sha256===hash(fixture)
                    &&f.expected.image_context_sha256===hash(c.image.context)&&f.expected.source_context_sha256===hash(c.image.context.source_context)
                    &&same(c.image.expected,{loaded_expected_sha256:hash(f.expected),image_context_sha256:hash(c.image.context),source_context_sha256:hash(c.image.context.source_context)}));
                need(c.image.entries.length===3);
                const price=reservation(c,p,s,packed,schemas),m=price.m;
                const prepare=m.input_prepare_copies*price.input+m.prepare_context_copies*price.context;
                const running=m.input_live_copies*price.input+m.context_copies*price.context;
                const support=Math.max(m.schema_copies*price.schemaBytes,m.schema_startup_raw_forms*price.rawMax+m.schema_startup_copies*price.schemaBytes);
                const metadata=Math.max(prepare,running)+m.output_copies*p.output_bytes+m.frame_copies*r.frame_bytes+m.inventory_copies*price.inventory+support;
                need(same(r,{input_context_bytes:price.input,context_bytes:price.context,support_schema_bytes:price.schemaBytes,support_raw_max:price.rawMax,
                    frame_bytes:r.frame_bytes,image_metadata_upper:price.inventory,
                    metadata_bytes:metadata,known_input_bytes:metadata+m.entry_forms*price.raw+m.declaration_forms*price.declared+m.source_forms*price.source,
                    output_bytes:p.output_bytes})&&Number.isSafeInteger(r.frame_bytes)&&r.frame_bytes>0
                    &&r.frame_bytes<=c.stream.profiles.private_owned_config_handoff.max_request_bytes
                    &&r.metadata_bytes<=p.metadata_bytes&&r.known_input_bytes<=p.image_bytes);
                need(same(c.stream.manifest,c.image.context.admission.compiler_manifest));
                const stream=c.stream;
                gate=declaration.createCompilerDeclarationTextHandoffPreparation(stream.expected,stream.manifest,stream.profiles,stream.schema,stream.preparation_schema,{deadline});
                stage='entry';guard=installGuard(f,p,check);
                const ownerProfiles=Object.fromEntries(['private_compiler_entry_owner','compiler_library_inputs','private_input_image_experiment'].map(k=>[k,ep[k]]));
                owner=entry.createCompilerEntryOwner(f.root,f.owner_expected,ownerProfiles,f.schemas.entry,{deadline});
                stage='evaluation';loaded=bridge.evaluateFixtureCompilerEntry(owner,f.expected,ep,f.schemas,{deadline});owner=null;
                bootstrap={version:p.version,context_sha256:held.context_sha256};
                stage='stream';await send({kind:'ready',...bootstrap});
            }else{
                need(chunk.length<=held.reservation.frame_bytes);
                const ack=gate.feed(chunk);
                if(ack!==null){await send(ack);gate.release_credit(ack);}
            }
        }
        need(gate&&loaded);finalAck=gate.end_input();stage='image';
        prepared=gate.prepare();
        const c=held.expanded,inputs={namespace_complete:true,entries:c.image.entries.map(r=>({path:r.path,role:r.role,raw:Buffer.from(r.utf8Base64,'base64')}))};
        const value=bridge.constructPreparedImageFromFixtureApi(loaded,c.image.expected,c.image.context,inputs,prepared);
        loaded=prepared=null;inputs.entries.length=0;
        need(value.joint_metadata_reserved_bytes<=held.reservation.metadata_bytes&&value.known_joint_inputs_reserved_bytes<=held.reservation.known_input_bytes);
        const snapshot=gate.snapshot();gate.dispose();gate=null;
        need(snapshot.raw_stager_state==='DISPOSED'&&snapshot.retained_text_records===0&&guard.settled()
            &&guard.counts.opens===5&&guard.counts.closes===5&&guard.counts.entry_opens===1&&guard.counts.forbidden===0);
        const expected=c.stream.expected;
        result={kind:'result',version:held.profile.version,status:held.profile.result_status,context_sha256:held.context_sha256,
            session_id:expected.session_id,manifest_sha256:expected.manifest_sha256,inventory_sha256:expected.inventory_sha256,
            context_sha256_stream:expected.context_sha256,loaded:value,reservation:held.reservation,
            role_opens:guard.counts.opens,role_closes:guard.counts.closes,entry_opens:guard.counts.entry_opens,
            forbidden_attempts:guard.counts.forbidden,node_version:process.version};
        validate(result,held.schema.$defs.result,held.schema,'fixture_process_result');check();
    }catch(error){failed=error;}
    finally{
        let clean=true;
        for(const dispose of [()=>entry._disposeCompilerEntry(owner),()=>loaded?.dispose(),
            ()=>declaration._disposePreparedCompilerTextSet(prepared),()=>gate?.dispose(),()=>guard?.cleanup()]){
            try{if(dispose()===false&&guard&&!guard.settled())clean=false;}catch{clean=false;}
        }
        owner=loaded=prepared=gate=prefix=null;
        cleanupSettled=clean;
        if(!clean){failed??=Error('fixture cleanup');result=null;}
    }
    try{
        if(failed){
            const component=typeof failed.code==='string'&&/^(entry|fixture|text_preparation|stream_stager|image)_[a-z0-9_]{1,80}$/.test(failed.code)?failed.code:'component_refused';
            // Only fixed stage/code metadata survives; no source, path or error text.
            const raw=JSON.stringify({kind:'refused',stage,component,cleanup_settled:cleanupSettled})+'\n';
            if(Buffer.byteLength(raw)<=cap)await new Promise(resolve=>process.stdout.write(raw,resolve));
            process.exitCode=1;return;
        }
        need(result);stage='publication';await send(finalAck);await send(result);
        await new Promise(resolve=>process.stdout.end(resolve));check();
    }finally{
        clearTimeout(timer);held=result=bootstrap=schemas=null;
    }
}
if(require.main===module)run().catch(()=>{process.exitCode=1;process.stdin.destroy();process.stdout.destroy();process.stderr.end('fixture_image_endpoint_refused\n');});
module.exports={run};

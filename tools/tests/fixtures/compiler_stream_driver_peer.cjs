'use strict';
// Adversarial synthetic peer only, never an alternate runtime endpoint.
const mode = process.argv[4];
if (mode === 'guarded') {
    const { run } = require('../../core/compiler_stream_endpoint.cjs');
    const Module = require('node:module'), fs = require('node:fs');
    const load = Module._load;
    Module._load = function (name,...args) {
        if (!['node:crypto','node:perf_hooks','node:util'].includes(name)) throw Error('forbidden module');
        return load.call(this,name,...args);
    };
    for (const name of ['readFileSync','readFile','openSync','open','statSync','stat','readdirSync','readdir','writeFileSync','writeFile']) fs[name] = () => { throw Error('forbidden fs'); };
    run().catch(() => { process.exitCode=1; process.stdin.destroy(); process.stdout.destroy(); });
} else {
    let pending='', bootstrap=null, count=0, raw=0, files=0, summary;
    const send=value=>process.stdout.write(JSON.stringify(value)+'\n');
    const crypto=require('node:crypto');
    process.stdin.on('data',chunk=>{
        pending+=chunk.toString('utf8');
        for (;;) {
            const end=pending.indexOf('\n');if(end<0)break;
            const text=pending.slice(0,end);pending=pending.slice(end+1);const row=JSON.parse(text);
            if (!bootstrap) {
                bootstrap=row;
                if (['stall','cancel'].includes(mode)) { process.stdin.pause(); setInterval(()=>{},1000); return; }
                if(mode==='exit_bootstrap') {process.exit(2);return;}
                const ready={kind:'ready',version:row.version,session_id:row.expected.session_id,manifest_sha256:row.expected.manifest_sha256};
                if(mode==='wrong_ready')ready.session_id='foreign';
                if(mode==='oversize') {process.stdout.write('x'.repeat(row.profiles.private_input_image_experiment.limits.metadata_bytes+1));return;}
                if(mode==='stderr_flood') {process.stderr.write('private-test-marker'.repeat(row.profiles.private_input_image_experiment.limits.metadata_bytes));return;}
                if(mode==='truncated') {process.stdout.end('{"kind":');process.stdin.destroy();return;}
                if(mode==='malformed') {process.stdout.end('not-json\n');process.stdin.destroy();return;}
                if(mode==='duplicate_json') {process.stdout.end('{"kind":"ready","kind":"ready"}\n');process.stdin.destroy();return;}
                if(mode==='nonfinite') {process.stdout.end('{"kind":NaN}\n');process.stdin.destroy();return;}
                send(ready);
                continue;
            }
            count++;
            if(row.kind==='open') {
                files=row.manifest.files.length;raw=row.manifest.files.reduce((n,file)=>n+file.bytes,0);
                const hash=crypto.createHash('sha256');
                for(const file of row.manifest.files)hash.update(JSON.stringify([file.name,file.bytes,file.raw_sha256])+'\n');
                summary=hash.digest('hex');
            }
            const ack={version:row.version,session_id:row.session_id,manifest_sha256:row.manifest_sha256,seq:row.seq,
                frame_sha256:crypto.createHash('sha256').update(text+'\n').digest('hex'),state:row.kind==='commit'?'COMPLETE':'STAGED'};
            if(row.kind!=='commit') {
                if(mode==='wrong_ack')ack.frame_sha256='0'.repeat(64);
                if(mode==='lost_ack')return;
                send(ack);
                if(mode==='backpressure_cancel' && row.kind==='open') {process.stdin.pause();setInterval(()=>{},1000);return;}
                if(mode==='duplicate_ack')send(ack);
            } else bootstrap.finalAck=ack;
        }
    });
    process.stdin.on('end',()=>{
        if(!bootstrap?.finalAck) {process.exitCode=2;return;}
        send(bootstrap.finalAck);
        const descriptor=bootstrap.profiles.private_compiler_stream_driver;
        const result={kind:'result',version:descriptor.version,status:descriptor.result_status,
            ...Object.fromEntries(['session_id','manifest_sha256','context_sha256','inventory_sha256'].map(k=>[k,bootstrap.expected[k]])),
            node_version:process.version,files_completed:files,raw_bytes_staged:raw,content_summary_sha256:summary,receiver_state:'DISPOSED',owned_buffer_bytes:0,
            raw_bytes_verified_in_model:true,real_source_transfer:false,raw_content_verified:false,host_text_verified:false,
            production_enabled:false,native_execution_attested:false,semantic_lineage:false,cache_eligible:false};
        if(mode==='result_drift')result.inventory_sha256='0'.repeat(64);
        if(mode==='promoted_result')result.production_enabled=true;
        if(mode==='wrong_result_type')result.files_completed=true;
        if(mode==='wrong_summary')result.content_summary_sha256='0'.repeat(64);
        if(mode==='wrong_count')result.raw_bytes_staged++;
        send(result);
        if(mode==='trailing')send({extra:true});
        if(mode==='nonzero_exit')process.exitCode=4;
        if(mode==='hang_after_result')setInterval(()=>{},1000);
        process.stdout.end();
        if(mode==='late_stderr')setTimeout(()=>process.stderr.end('private-test-marker'),20);
    });
}

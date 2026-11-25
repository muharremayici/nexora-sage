'use strict';
// Controlled test wrapper only; no runtime fault selector.
const mode=process.argv[4];
if(mode==='stall'){process.stdin.pause();setInterval(()=>{},1000);}
else{
    const image=require('../../core/ts_input_image_experiment.cjs');
    const {performance}=require('node:perf_hooks');
    const create=image.createPreparedCompilerExperiment;let disposed=0,constructors=0;
    const injected=new RangeError('controlled unexpected JSON API exception');
    image.createPreparedCompilerExperiment=(ts,...args)=>{
        constructors++;
        if(mode==='constructor_error')throw new RangeError('controlled synthetic constructor failure');
        if(mode==='external_reference')ts.preProcessFile=()=>({typeReferenceDirectives:[],importedFiles:[{fileName:'external'}],referencedFiles:[]});
        if(mode==='config_error')ts.parseConfigFileTextToJson=()=>({error:true});
        if(mode==='json_identity'||mode==='json_syntax'){
            const parse=ts.parseConfigFileTextToJson;
            ts.parseConfigFileTextToJson=(...values)=>{
                const nativeParse=JSON.parse;
                JSON.parse=()=>{throw mode==='json_identity'?injected:new SyntaxError('controlled JSON syntax');};
                try{return parse(...values);}finally{JSON.parse=nativeParse;}
            };
        }
        ts.createProgram=()=>{throw Error('Program forbidden');};
        const instance=create(ts,...args);
        if(mode==='after_image_deadline')Object.defineProperty(performance,'now',{value:()=>Number.MAX_SAFE_INTEGER});
        return {...instance,dispose(){disposed++;instance.dispose();if(mode==='dispose_error')throw Error('controlled dispose failure');}};
    };
    require('../../core/prepared_image_process.cjs');
    const {runImage}=require('../../core/compiler_stream_endpoint.cjs');
    if(mode==='guarded'){
        const fs=require('node:fs'),Module=require('node:module'),load=Module._load;
        Module._load=function(name,...args){
            if(!['./prepared_image_process.cjs','./compiler_declaration_text.cjs'].includes(name))throw Error('forbidden module');
            return load.call(this,name,...args);
        };
        for(const key of ['readFileSync','readFile','openSync','open','statSync','stat','readdirSync','readdir','writeFileSync','writeFile'])fs[key]=()=>{throw Error('forbidden late fs');};
    }
    const write=process.stdout.write.bind(process.stdout);
    process.stdout.write=(data,...args)=>{
        const row=JSON.parse(data.toString('utf8'));
        if(row.kind==='ready'&&mode==='wrong_ready')row.process_context_sha256='0'.repeat(64);
        if(row.kind==='result'){
            if(disposed!==1||constructors!==1)throw Error('image not disposed once before result');
            if(mode==='wrong_image')row.image_summary.image_sha256='0'.repeat(64);
            if(mode==='bool_nested_count')row.image_summary.file_count=true;
            if(mode==='promoted')row.image_summary.image_admitted=true;
            if(mode==='wrong_ref')row.source_context_sha256='0'.repeat(64);
            if(mode==='legacy_version')row.version='v1-private-synthetic-compiler-text-process';
            if(mode==='extra_text')row.hostText='private synthetic text';
            if(mode==='nonzero')process.exitCode=4;
            if(mode==='hang_result')setInterval(()=>{},1000);
            if(mode==='blocked_output'){setInterval(()=>{},1000);return false;}
            if(mode==='write_error'){args.find(value=>typeof value==='function')?.(Error('private write failure'));return false;}
            if(mode==='trailing'){write(JSON.stringify(row)+'\n',...args);return write('{"extra":true}\n');}
        }
        return write(JSON.stringify(row)+'\n',...args);
    };
    runImage().catch(error=>{
        process.exitCode=(mode==='json_identity'&&error!==injected||mode==='json_syntax'&&error.code!=='config_profile_refused')?90:1;
        process.stdin.destroy();process.stdout.destroy();process.stderr.end('synthetic_image_peer_refused\n');
    });
}

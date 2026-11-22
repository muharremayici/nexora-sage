'use strict';
// Test-only fault peer: never an admissible API body or production selector.
const vm=require('node:vm'),Module=require('node:module');
const mode=process.argv[4];process.argv=process.argv.slice(0,4);
const mark=()=>process.stderr.write('fixture fault reached\n');
if(mode==='broken_pipe')process.exit(1);
if(mode==='hang_compile')vm.compileFunction=()=>{mark();for(;;){}};
if(mode==='hang_construct'){
    const load=Module._load;
    Module._load=function(name,...args){const value=load.call(this,name,...args);
        if(name==='./ts_input_image_experiment.cjs')return {...value,createPreparedCompilerExperiment(){mark();for(;;){}}};
        return value;};
}
const write=process.stdout.write.bind(process.stdout),end=process.stdout.end.bind(process.stdout);
process.stdout.write=function(value,...args){
    let line=String(value),body;try{body=JSON.parse(line);}catch{}
    if(body?.kind==='result'){
        if(mode==='bad_result'){body.loaded.image_summary.image_sha256='0'.repeat(64);line=JSON.stringify(body)+'\n';}
        if(mode==='extra')line+='{}\n';
        if(mode==='truncated')line=line.slice(0,-1);
        if(mode==='nonzero')process.exitCode=7;
        if(mode==='stderr')process.stderr.write(Buffer.alloc(70000,120));
    }
    return write(line,...args);
};
if(mode==='no_eof')process.stdout.end=function(callback){mark();setInterval(()=>{},1000);if(typeof callback==='function')callback();};
require('../../core/fixture_image_endpoint.cjs').run().catch(()=>{
    process.exitCode=1;process.stdin.destroy();end();process.stderr.end('fixture peer refused\n');
});

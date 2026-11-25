'use strict';
// Private finite fixture: retained config bytes only. Not a product CLI.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'../../..');
const profiles=JSON.parse(fs.readFileSync(path.join(root,'config/analysis_snapshot_lineage_contract.json'),'utf8')).artifacts.ts_diagnostics;
const policy=profiles.private_owned_config_extends,config=profiles[policy.config_limit_profile];
const modulePath=require.resolve('typescript',{paths:[path.join(root,'tools/engines')]}),reference=crypto.createHash('sha256').update(fs.readFileSync(modulePath)).digest('hex');
const ts=require(modulePath),core=require('../../core/owned_config_handoff.cjs');
const {canonical}=require('../../core/owned_image_handoff.cjs');
const mode=process.argv[2],sha=x=>crypto.createHash('sha256').update(x).digest('hex');
const buffer=Buffer.alloc(config.max_request_bytes+1);let count=0,n;
while(count<buffer.length&&(n=fs.readSync(0,buffer,count,buffer.length-count,null)))count+=n;
if(count>config.max_request_bytes)throw Error('fixture frame over budget');
const text=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(buffer.subarray(0,count));
if(!text.endsWith('\n'))throw Error('fixture frame incomplete');
const frame=JSON.parse(text.slice(0,-1));if(canonical(frame)!==text.slice(0,-1)||Object.keys(frame).sort().join(',')!=='expected,request')throw Error('fixture frame invalid');
let programAttempts=0,ioAttempts=0,instance,result;
const runtime=Object.create(ts);Object.defineProperty(runtime,'createProgram',{value:()=>{programAttempts++;throw Error('Program forbidden');}});
const restore=[];
function modelApi(control){
    const property=(name,value)=>({kind:'property',name:{text:name},initializer:value});
    const object=properties=>({kind:'object',properties});
    const literal={kind:'string',text:policy.extends_literal,getStart:tree=>tree.text.indexOf(JSON.stringify(policy.extends_literal)),end:0};
    const rootValue={extends:policy.extends_literal,files:[policy.declared_source_name]};
    const baseValue={compilerOptions:{strict:true}};
    if(control==='root_shape')rootValue.extends=[policy.extends_literal];
    if(control==='base_shape')baseValue.extends=policy.extends_literal;
    const tree=(name,text,value,properties)=>({fileName:name,text,value,parseDiagnostics:[],children:[object(properties)],
        statements:[{expression:object(properties)}],getLineAndCharacterOfPosition(start){const rows=text.slice(0,start).split('\n');return {line:rows.length-1,character:rows.at(-1).length};}});
    const api={version:ts.version,isObjectLiteralExpression:n=>n.kind==='object',isPropertyAssignment:n=>n.kind==='property',isStringLiteral:n=>n.kind==='string',
        forEachChild(n,callback){for(const child of n.children||[])callback(child);},convertToObject:t=>t.value,
        readJsonConfigFile(name,read){const text=read(name),props=[property('extends',literal)];literal.end=literal.getStart({text})+JSON.stringify(policy.extends_literal).length;
            if(control==='duplicate')props.push(property('extends',literal));
            return tree(name,text,rootValue,props);},
        parseJsonSourceFileConfigFileContent(source,host,base,unused,name,stack,extensions,cache){
            host.fileExists(policy.roster.base.virtual_path);const text=host.readFile(policy.roster.base.virtual_path);
            if(control==='repeat')host.readFile(policy.roster.base.virtual_path);
            if(['third','swallowed'].includes(control)){
                try{host.readFile(base+'/third.json');}catch(error){if(control==='third')throw error;}
            }
            if(control==='realpath')host.realpath(policy.roster.base.virtual_path);
            if(control==='glob')host.readDirectory(base);
            const baseTree=tree(policy.roster.base.virtual_path,text,baseValue,[]);
            if(control==='cache_target')baseTree.fileName=base+'/foreign.json';
            if(control==='cache_text')baseTree.text+='changed';
            cache.set(baseTree.fileName,{extendedResult:baseTree});
            if(control==='cache_extra')cache.set(base+'/extra.json',{extendedResult:{...baseTree}});
            source.extendedSourceFiles=[policy.roster.base.virtual_path];
            return {options:{strict:control==='options'?false:true},fileNames:[base+'/'+policy.declared_source_name],errors:control==='diagnostics'?[{code:1234}]:[]};
        }};
    return api;
}
try{
    let api=runtime;
    if(mode.startsWith('model-'))api=modelApi(mode.slice(6));
    for(const key of ['readFileSync','openSync','statSync','lstatSync','readdirSync','existsSync','realpathSync']){
        const original=fs[key];fs[key]=()=>{ioAttempts++;throw Error('fixture live I/O forbidden');};restore.push(()=>{fs[key]=original;});
    }
    if(mode==='admission'){
        const admitted=core.admitConfigExtends(api,frame.request,frame.expected,profiles,reference);
        result={status:'PRIVATE_CONFIG_ADMISSION_ONLY',binding_sha256:admitted.identity};
    }else{
        instance=core.createConfigExtendsExperiment(api,frame.request,frame.expected,profiles,reference);
        if(mode==='model-policy')profiles.private_owned_config_extends={...policy,production_enabled:true};
        result=instance.replay(mode==='model-context'?{...frame.expected,invocation_id:'other'}:frame.expected);
        let repeated=false,disposed=false;
        try{instance.replay(frame.expected);}catch(e){repeated=e.code==='config_extends_already_used';}
        instance.dispose();try{instance.replay(frame.expected);}catch(e){disposed=e.code==='config_extends_disposed';}
        result={...result,repeated_parse_refused:repeated,disposed_refused:disposed};
    }
}catch(error){result={status:'PRIVATE_CONFIG_FIXTURE_REFUSED',code:error.code||'config_fixture_failure'};}
finally{for(const undo of restore)undo();if(instance)instance.dispose();}
process.stdout.write(JSON.stringify({result,native_config_parser:mode==='native',modeled_only:mode.startsWith('model-'),
    program_attempts:programAttempts,live_io_attempts:ioAttempts}));

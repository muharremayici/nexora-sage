'use strict';
// Synthetic constructor only. A compiler API double is not installed TypeScript.
const fs = require('node:fs');
const request = JSON.parse(fs.readFileSync(0,'utf8'));
const { createExperiment, ImageRefusal } = require('../../core/ts_input_image_experiment.cjs');
const Module = require('node:module');
let forbidden = 0, configCalls = 0, preprocessCalls = 0, programCalls = 0, instance;
const reject = () => { forbidden++; throw Error('forbidden access'); };
Module._load = reject;
for (const key of ['readFileSync','readFile','openSync','open','statSync','stat','readdirSync','readdir','writeFileSync','writeFile']) fs[key] = reject;
if (Object.hasOwn(request,'ambient_raw_length')) globalThis.raw = {length:request.ambient_raw_length};
else delete globalThis.raw;
const tsDouble = {
    version:'0.0.0-synthetic',
    createProgram() { programCalls++; throw Error('program forbidden'); },
    parseConfigFileTextToJson(path,text) {
        configCalls++;
        return {config:JSON.parse(text)};
    },
    preProcessFile() {
        preprocessCalls++;
        return {typeReferenceDirectives:[],importedFiles:[],referencedFiles:[]};
    }
};
let result;
try {
    const entries = request.entries.map(row => ({path:row.path,role:row.role,raw:Buffer.from(row.raw_base64,'base64')}));
    instance = createExperiment(tsDouble,{entries,namespace_complete:true},request.binding,request.policy);
    result = {status:'CONSTRUCTED_WITH_COMPILER_API_DOUBLE',summary:instance.summary};
} catch (error) {
    result = {status:'REFUSED',error:error instanceof ImageRefusal ? error.code : error.name,
        unbound_raw_reference:error instanceof ReferenceError && error.message === 'raw is not defined'};
} finally { instance?.dispose(); delete globalThis.raw; }
process.stdout.write(JSON.stringify({...result,node_version:process.version,constructor_calls:1,
    config_double_calls:configCalls,preprocess_double_calls:preprocessCalls,program_calls:programCalls,
    forbidden_accesses:forbidden,typescript_module_loads:0,api_double:true})+'\n');

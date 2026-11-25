'use strict';
// New small-budget argument probes of the unchanged copier, not a product receipt.
const fs=require('node:fs'),Module=require('node:module');
const request=JSON.parse(fs.readFileSync(0,'utf8'));
const {copy}=require('../../core/compiler_text_window.cjs');
let forbidden=0;
for(const k of ['readFileSync','readFile','openSync','open','statSync','writeFileSync'])fs[k]=()=>{forbidden++;throw Error('probe I/O');};
Module._load=()=>{forbidden++;throw Error('probe module load');};
let value=request.input;
if(request.alias){const leaf={a:1};value={left:leaf,right:leaf};}
const budget={nodes:0,bytes:0};let outcome='threw',code=null,distinct=null;
try{const result=copy(value,'copy_probe',request.ceiling,new Set(),0,budget);outcome='returned';if(request.alias)distinct=result.left!==result.right;}
catch(error){code=error.code??null;}
process.stdout.write(JSON.stringify({unchanged_helper_probe:true,root_copy_invocations:1,root_copy_outcome:outcome,
    helper_node_charges:budget.nodes,logical_scalar_key_byte_charges:budget.bytes,code,alias_copies_distinct:distinct,forbidden_io_calls:forbidden})+'\n');

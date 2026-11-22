'use strict';
// Deliberately invalid pipe fixtures. No target paths or payload files exist.
const fs=require('node:fs');
fs.readFileSync(0);
switch(process.argv[5]){
case 'extra':fs.writeSync(1,'{}\n{}\n');break;
case 'truncated':fs.writeSync(1,'{"bad":');break;
case 'duplicate':fs.writeSync(1,'{"a":1,"a":2}\n');break;
case 'nonfinite':fs.writeSync(1,'{"a":NaN}\n');break;
case 'invalid_utf8':fs.writeSync(1,Buffer.from([255,10]));break;
case 'oversize':fs.writeSync(1,'x'.repeat(Number(process.argv[3])+1));break;
case 'stderr':fs.writeSync(1,'{}\n');fs.writeSync(2,'synthetic diagnostic');break;
case 'exit':process.exitCode=3;break;
case 'hang':setInterval(()=>{},1000);break;
case 'shape':fs.writeSync(1,'{}\n');break;
default:process.exitCode=5;
}

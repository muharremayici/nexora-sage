'use strict';
// First-party synthetic fault loader, never selected through the product API.
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(process.argv.pop()).toLowerCase(),mode=process.argv.pop();
const n={open:fs.openSync,read:fs.readSync,close:fs.closeSync,lstat:fs.lstatSync};
const owned=new Set();let failed=false;
const target=p=>typeof p==='string'&&path.resolve(p).toLowerCase().startsWith(root+path.sep);
fs.openSync=(p,...rest)=>{
    if(target(p)&&mode==='open'&&!failed){failed=true;throw new Error('synthetic open failure');}
    const fd=n.open(p,...rest);
    if(target(p)&&mode==='open_descriptor'&&!failed){failed=true;n.close(fd);return 1;}
    if(target(p))owned.add(fd);return fd;
};
fs.readSync=(fd,b,o,len,pos)=>{
    if(owned.has(fd)){
        if(mode==='read'&&!failed){failed=true;throw new Error('synthetic read failure');}
        if(mode==='invalid_count'&&!failed){failed=true;return len+1;}
        if(mode==='partial')len=Math.min(len,3);
    }
    return n.read(fd,b,o,len,pos);
};
fs.closeSync=fd=>{
    const isOwned=owned.has(fd);const result=n.close(fd);owned.delete(fd);
    if(isOwned&&mode==='close_unknown'&&!failed){failed=true;throw new Error('synthetic unknown close outcome');}
    return result;
};
fs.lstatSync=p=>{
    const result=n.lstat(p);
    if(target(p)&&mode==='alias'&&!failed){failed=true;return {isDirectory:()=>result.isDirectory(),isFile:()=>result.isFile(),isSymbolicLink:()=>true};}
    return result;
};
let textHashes=0;
const crypto=require('node:crypto'),originalHash=crypto.createHash;
crypto.createHash=(algorithm,...args)=>{
    const h=originalHash(algorithm,...args),update=h.update;
    h.update=function(value,...rest){
        if(typeof value==='string'&&value.includes('__FORBIDDEN_EXECUTION__')&&mode==='use_throw'&&++textHashes===2)throw new Error('synthetic hash outcome unknown');
        return update.call(this,value,...rest);
    };
    return h;
};
require('../../core/compiler_entry_same_read_use_child.cjs');
if(globalThis.__FORBIDDEN_EXECUTION__)process.exitCode=47;

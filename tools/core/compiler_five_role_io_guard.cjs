'use strict';
// Finite filesystem gate shared by fixed endpoints; never selects a consumer.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const Module=require('node:module'),vm=require('node:vm');
const norm=v=>process.platform==='win32'?path.resolve(v).toLowerCase():path.resolve(v);
const inside=(a,b)=>{const r=path.relative(a,b);return r!==''&&r!=='..'&&!r.startsWith('..'+path.sep)&&!path.isAbsolute(r);};
function installFiveRoleGuard(...args){return installAtAncestor(norm(os.tmpdir()),...args);}
function installActualFiveRoleGuard(...args){
    const root=args[0].probe.root;
    args[4](typeof root==='string'&&path.isAbsolute(root),'reader_temporary_root');
    return installAtAncestor(norm(path.parse(root).root),...args);
}
function installAtAncestor(temporary,q,e,names,check,need,fail,deny,a,meta,roles,fds,native){
    const root=norm(q.probe.root);
    need(typeof q.probe.root==='string'&&path.isAbsolute(q.probe.root)&&inside(temporary,root)
        &&e.root_text===root&&q.request.root_text===root&&names.length===5,'reader_temporary_root');
    const files=new Map();
    for(const role of names){
        check();const row=e.roles[role],r=q.request.roles[role];
        need(r&&r.relative_path===row.relative_path&&r.max_raw_bytes===row.max_raw_bytes
            &&r.expectation.sha256===row.expected_sha256&&r.expectation.anchor_ref_sha256===row.anchor_ref_sha256,'reader_scope');
        const target=norm(path.join(root,...row.relative_path.split('/')));
        need(inside(root,target)&&!files.has(target),'reader_scope');files.set(target,{role,cap:row.max_raw_bytes});
    }
    const pkg=norm(path.dirname([...files].find(([,v])=>v.role==='installed_package')[0]));
    const knownMeta=n=>n===temporary||n===root||inside(temporary,n)&&inside(n,root)
        ||[...files.keys()].some(f=>n===f||inside(root,n)&&inside(n,f));
    function lstat(n){
        check();need(knownMeta(n),'reader_scope');meta.attempts++;
        try{const s=native.lstatSync(n);meta.returns++;return s;}catch(error){meta.unknown++;throw error;}
    }
    function components(n){
        const base=n===root?temporary:root;let cursor=base;
        let s=lstat(cursor);need(s.isDirectory()&&!s.isSymbolicLink(),'reader_alias');
        for(const part of path.relative(base,n).split(path.sep).filter(Boolean)){
            cursor=path.join(cursor,part);s=lstat(norm(cursor));
            need(!s.isSymbolicLink()&&(norm(cursor)===n&&files.has(n)?s.isFile():s.isDirectory()),'reader_alias');
        }
    }
    const metadataCall=(method,value)=>{
        check();need(typeof value==='string','reader_scope');const n=norm(value);
        need(method==='statSync'?n===root:method==='realpathSync'?(n===root||n===pkg||files.has(n)):knownMeta(n),'reader_scope');
        if(method==='realpathSync')components(n);
        meta.attempts++;try{const result=native[method](value);meta.returns++;
            if(method==='realpathSync')need(norm(result)===n,'reader_alias');return result;
        }catch(error){meta.unknown++;throw error;}
    };
    fs.realpathSync=value=>metadataCall('realpathSync',value);fs.statSync=value=>metadataCall('statSync',value);
    fs.lstatSync=value=>metadataCall('lstatSync',value);
    fs.openSync=(value,mode,...rest)=>{
        check();need(typeof value==='string','reader_scope');const n=norm(value),scope=files.get(n);
        need(scope&&mode==='r'&&a.open_attempts<5&&!roles.some(r=>r.role===scope.role),'reader_scope');components(n);a.open_attempts++;
        let fd;try{fd=native.openSync(value,mode,...rest);}catch(error){a.open_unknown++;throw error;}
        a.open_returns++;const r={role:scope.role,cap:scope.cap,known_delivered_bytes:0,read_attempts:0,closed:false,close_attempted:false,close_unknown:false};
        roles.push(r);
        if(!Number.isSafeInteger(fd)||fd<0||fds.has(fd)||[0,1,2].includes(fd)){a.open_unknown++;fail('reader_descriptor');}
        fds.set(fd,r);return fd;
    };
    fs.fstatSync=fd=>{check();need(fds.has(fd),'reader_descriptor');return native.fstatSync(fd);};
    fs.readSync=(fd,b,offset,length,position)=>{
        check();const r=fds.get(fd);need(r&&!r.close_attempted&&Number.isSafeInteger(length)&&length>=0
            &&length<=r.cap+1-r.known_delivered_bytes&&length<=e.max_delivered_bytes-a.known_delivered_bytes,'reader_read_budget');
        a.read_attempts++;r.read_attempts++;let n;
        try{n=native.readSync(fd,b,offset,length,position);}catch(error){a.read_unknown++;throw error;}
        if(!Number.isSafeInteger(n)||n<0||n>length){a.read_unknown++;fail('reader_read_unknown');}
        r.known_delivered_bytes+=n;a.known_delivered_bytes+=n;return n;
    };
    fs.closeSync=fd=>{
        const r=fds.get(fd);need(r&&!r.close_attempted,'reader_descriptor');r.close_attempted=true;a.close_attempts++;
        try{native.closeSync(fd);}catch(error){r.close_unknown=true;a.close_unknown++;throw error;}
        r.closed=true;fds.delete(fd);a.close_returns++;
    };
    for(const k of ['readFileSync','readFile','open','readdirSync','readdir','writeFileSync','writeFile','appendFileSync','writeSync'])fs[k]=deny;
    Module._load=deny;for(const k of ['compileFunction','createContext','runInContext','runInNewContext','runInThisContext','Script'])vm[k]=deny;
 }
function settleFiveRoleGuard(a,fds,native){
    for(const [fd,r]of fds){
        if(r.close_attempted)continue; // Unknown close is not an unattempted close.
        r.close_attempted=true;a.emergency_closes++;a.close_attempts++;
        try{native.closeSync(fd);r.closed=true;fds.delete(fd);a.close_returns++;}catch{r.close_unknown=true;a.close_unknown++;}
    }
 }
module.exports={installFiveRoleGuard,installActualFiveRoleGuard,settleFiveRoleGuard};

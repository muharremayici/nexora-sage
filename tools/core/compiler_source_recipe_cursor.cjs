'use strict';
// Internal bounded source cursor. No owner, IO, loader or execution authority.
const crypto=require('node:crypto');
const {_canonicalCompilerStreamMetadata:canonical}=require('./compiler_stream_stager.cjs');
const sha=value=>crypto.createHash('sha256').update(value).digest('hex'),hash=value=>sha(canonical(value)),bytes=value=>Buffer.byteLength(canonical(value),'utf8');
class CompilerExportRecipeRefusal extends Error{constructor(code){super(code);this.code=code;}}
const need=(v,code)=>{if(!v)throw new CompilerExportRecipeRefusal(code);};
function sum(...vs){const n=vs.reduce((a,b)=>a+b,0);need(vs.every(v=>Number.isSafeInteger(v)&&v>=0)&&Number.isSafeInteger(n),'recipe_budget');return n;}
const SLOT_NAMES=['get','set','value','enumerable','configurable','writable'];
const RESERVED=new Set(('break case catch class const continue debugger default delete do else enum export extends false finally for function if implements import in instanceof interface let new null package private protected public return static super switch this throw true try typeof var void while with yield await').split(' '));
class SourceGap extends Error{constructor(code,start,end){super(code);this.code=code;this.start=start;this.end=end;}}
function expectedToken(t,value,kind='punct'){if(t.value!==value||t.kind!==kind)throw new SourceGap('unsupported_structure',t.start,t.end);return t;}
function referenceToken(t){if(t.kind!=='word'||RESERVED.has(t.value))throw new SourceGap('unsupported_identifier',t.start,t.end);return t;}
function nameToken(t){if(!['word','string'].includes(t.kind)||!t.value||t.value==='__proto__')throw new SourceGap('unsupported_name',t.start,t.end);return t;}
const punctToken=(t,value)=>t.kind==='punct'&&t.value===value;
function arrowTail(expect,identifier,peek,last){
    const arrowStart=expect('(').start;expect(')');const parametersEnd=last().end,arrow=peek();
    if(arrow.start.line!==parametersEnd.line)throw new SourceGap('arrow_line_terminator',arrow.start,arrow.end);
    expect('=>');return {arrowStart,reference:identifier()};
}

// A cursor, not a JavaScript parser. Only this closed statement grammar can complete.
function recognizeInternal(text,limits,check,metrics,helperSite,windowInfo=null,rowFragment=false,rowLocator=false,grammarIdentity=null){
    let i=0,b=0,line=1,column=0,previousCR=false,look=null,last=null,rows=[],names=new Set(),nameBytes=0;
    const position=()=>({u:i,b,line,column});
    const charge=n=>{metrics.work_units=sum(metrics.work_units,n);need(metrics.work_units<=limits.work_units,'recipe_budget');check();};
    const advance=()=>{
        const cp=text.codePointAt(i),n=cp>65535?2:1,c=text[i],start=helperSite?position():null;charge(n);
        b+=Buffer.byteLength(text.slice(i,i+n),'utf8');i+=n;metrics.scan_code_units=i;
        need(i<=limits.scan_code_units,'recipe_budget');
        if(c==='\r'){line++;column=0;previousCR=true;}
        else if(c==='\n'){if(!previousCR)line++;column=0;previousCR=false;}
        else{column+=n;previousCR=false;}
        if(helperSite&&(cp===0x2028||cp===0x2029))gap('unsupported_line_separator',start,position());
    };
    const gap=(code,start=look?.start||position(),end=look?.end||position())=>{throw new SourceGap(code,start,end);};
    const skip=()=>{
        while(i<text.length){
            if(' \t\r\n'.includes(text[i])){advance();continue;}
            if(text.startsWith('//',i)){while(i<text.length&&text[i]!=='\r'&&text[i]!=='\n')advance();continue;}
            if(text.startsWith('/*',i)){const start=position();advance();advance();while(i<text.length&&!text.startsWith('*/',i))advance();
                if(i===text.length)gap('unclosed_comment',start,position());advance();advance();continue;}
            break;
        }
    };
    const lex=()=>{
        skip();const start=position();if(i===text.length)return {kind:'eof',value:'',start,end:start};
        const first=text[i];let kind,value;
        if(/[A-Za-z_$]/.test(first)){
            kind='word';do{advance();need(i-start.u<=limits.cursor_bytes,'recipe_budget');}while(i<text.length&&/[A-Za-z0-9_$]/.test(text[i]));value=text.slice(start.u,i);
        }else if(first==='"'||first==="'"){
            kind='string';advance();const valueStart=i;
            while(i<text.length&&text[i]!==first){
                const c=text.charCodeAt(i);if(c<32||c>126||text[i]==='\\'){const bad=position();advance();gap('unsupported_string',bad,position());}
                advance();need(i-valueStart<=limits.cursor_bytes,'recipe_budget');
            }
            if(i===text.length)gap('unclosed_string',start,position());value=text.slice(valueStart,i);advance();
        }else if(text.startsWith('=>',i)){kind='punct';advance();advance();value='=>';}
        else if('.(){}:,;='.includes(first)){kind='punct';advance();value=first;}
        else{advance();gap('unsupported_token',start,position());}
        metrics.token_count++;need(metrics.token_count<=limits.token_count,'recipe_budget');return {kind,value,start,end:position()};
    };
    const peek=()=>look||(look=lex());
    const take=()=>{const t=peek();look=null;last=t;return t;};
    const expect=(value,kind)=>{expectedToken(peek(),value,kind);return take();};
    const identifier=()=>{referenceToken(peek());return take();};
    const name=()=>{nameToken(peek());return take();};
    const span=(start,end)=>{
        charge(end.u-start.u);const result={start_utf16:start.u,end_utf16:end.u,start_utf8:start.b,end_utf8:end.b,
            line_1based:start.line,column_utf16_0based:start.column,slice_text_utf8_sha256:sha(text.slice(start.u,end.u))};
        if(windowInfo){
            const a=sum(windowInfo.origin,start.u),b=sum(windowInfo.origin,end.u);need(b<=windowInfo.end,'recipe_identity');
            Object.assign(result,{absolute_start_utf16:a,absolute_end_utf16:b,global_start_utf8:null,global_end_utf8:null,global_line:null,global_column:null});
        }
        return result;
    };
    const tokenSpan=t=>span(t.start,t.end);
    const callHead=method=>{expect('Object','word');expect('.');expect(method,'word');expect('(');};
    const target=local=>{const t=identifier();if(t.value!==local)gap('target_linkage',t.start,t.end);return t;};
    const slots=()=>{
        expect('{');const out=[],seen=new Set();
        if(punctToken(peek(),'}'))gap('empty_descriptor');
        while(true){
            const key=name();if(!SLOT_NAMES.includes(key.value)||seen.has(key.value))gap('unsupported_slot',key.start,key.end);seen.add(key.value);expect(':');
            const start=peek().start;let syntax_kind,boolean_value=null,reference_identifier=null;
            if(['enumerable','configurable','writable'].includes(key.value)){
                const value=peek();if(value.kind!=='word'||!['true','false'].includes(value.value))gap('nonliteral_flag');
                boolean_value=take().value==='true';syntax_kind='boolean_literal';
            }else{
                if(key.value!=='value'){expect('(');expect(')');expect('=>');syntax_kind='arrow_identifier_return';}
                else syntax_kind='identifier_expression';
                reference_identifier=identifier().value;
            }
            out.push({slot:key.value,syntax_kind,boolean_value,reference_identifier,key_span:tokenSpan(key),value_span:span(start,last.end)});
            if(!punctToken(peek(),','))break;expect(',');if(punctToken(peek(),'}'))break;
        }
        expect('}');if((seen.has('get')||seen.has('set'))&&(seen.has('value')||seen.has('writable')))gap('mixed_descriptor',last.start,last.end);
        return {slots:out,omitted_slots:SLOT_NAMES.filter(k=>!seen.has(k))};
    };
    const arrowRows=fragment=>{
            while(true){
                const key=name();if(names.has(key.value))gap('duplicate_name',key.start,key.end);names.add(key.value);
                nameBytes=sum(nameBytes,Buffer.byteLength(key.value,'utf8'));need(nameBytes<=limits.name_utf8_bytes,'recipe_budget');
                expect(':');const {arrowStart,reference}=arrowTail(expect,identifier,peek,()=>last);
                rows.push({row_index:rows.length,kind:'source_name_candidate',name:key.value,key_kind:key.kind,key_span:tokenSpan(key),
                    entry_span:span(key.start,reference.end),arrow_span:span(arrowStart,reference.end),
                    reference:{identifier:reference.value,binding_resolved:false,span:tokenSpan(reference)}});
                need(rows.length<=limits.row_count&&bytes(rows)<=limits.fact_bytes,'recipe_budget');
                if(!punctToken(peek(),','))break;expect(',');if(fragment?peek().kind==='eof':punctToken(peek(),'}'))break;
            }
    };
    const locate=()=>{
        let queue=[],queuedBytes=0,previous=null,frontier=position(),unknown=null,eof=false,atBeginning=true;
        const fill=()=>{
            while(queue.length<7&&!eof&&!unknown){
                let token;
                try{token=lex();}catch(error){if(!(error instanceof SourceGap))throw error;unknown=error;break;}
                const n=Buffer.byteLength(token.value,'utf8');need(sum(queuedBytes,n)<=limits.cursor_bytes,'recipe_budget');
                queue.push(token);queuedBytes+=n;frontier=token.end;eof=token.kind==='eof';
                metrics.lookahead_peak_bytes=Math.max(metrics.lookahead_peak_bytes,queuedBytes);
            }
        };
        const drop=()=>{const t=queue.shift();queuedBytes-=Buffer.byteLength(t.value,'utf8');previous=t.kind==='punct'?t.value:null;atBeginning=false;charge(1);};
        try{
            metrics.fact_bytes=2;need(metrics.fact_bytes<=limits.fact_bytes,'recipe_budget');
            while(true){
                fill();if(!queue.length||queue[0].kind==='eof')break;
                let match=null;
                if(queue.length===7&&(atBeginning||previous==='{'||previous===',')){
                    // BOF includes permitted leading trivia, without treating later word tokens as a new origin.
                    try{
                        const key=nameToken(queue[0]);let index=1;
                        const expectPart=(value,kind)=>{const t=queue[index];charge(1+Buffer.byteLength(t.value,'utf8'));expectedToken(t,value,kind);index++;return t;};
                        const identifierPart=()=>{const t=queue[index];charge(1+Buffer.byteLength(t.value,'utf8'));referenceToken(t);index++;return t;};
                        charge(1+Buffer.byteLength(key.value,'utf8'));expectPart(':');
                        const tail=arrowTail(expectPart,identifierPart,()=>queue[index],()=>queue[index-1]);
                        const right=queue[index];charge(1);
                        if(right.kind==='eof'||right.kind==='punct'&&[',','}'].includes(right.value))match={key,reference:tail.reference};
                    }catch(error){if(!(error instanceof SourceGap))throw error;}
                }
                if(match){
                    need(rows.length<limits.row_count,'recipe_budget');
                    const keyBytes=Buffer.byteLength(match.key.value,'utf8');nameBytes=sum(nameBytes,keyBytes);need(nameBytes<=limits.name_utf8_bytes,'recipe_budget');
                    const width=match.reference.end.b-match.key.start.b;charge(width);
                    const s=span(match.key.start,match.reference.end),{slice_text_utf8_sha256,...coordinates}=s;
                    const record={occurrence_index:rows.length,...coordinates,view_utf16_units:s.end_utf16-s.start_utf16,
                        view_utf8_bytes:width,view_text_sha256:slice_text_utf8_sha256,single_row_grammar_policy_sha256:grammarIdentity};
                    const size=bytes(record);charge(size);const next=sum(metrics.fact_bytes,size,rows.length?1:0);need(next<=limits.fact_bytes,'recipe_budget');
                    rows.push(record);metrics.fact_bytes=next;metrics.name_utf8_bytes=nameBytes;
                    metrics.candidate_identity_bytes_hashed=sum(metrics.candidate_identity_bytes_hashed,width);
                    for(let n=0;n<6;n++)drop();match=null;
                }else{if(unknown&&queue.length<7)break;drop();}
            }
            let record=null;
            if(unknown){charge(unknown.end.b-unknown.start.b);record={code:unknown.code,...span(unknown.start,unknown.end)};
                metrics.gap_bytes=bytes(record);charge(metrics.gap_bytes);need(metrics.gap_bytes<=limits.gap_bytes,'recipe_budget');}
            const observation={scan_complete_within_model:!unknown,candidate_count:rows.length,candidates:rows,
                certified_prefix_end_utf16:frontier.u,certified_prefix_end_utf8:frontier.b,examined_through_utf16:i,examined_through_utf8:b,
                unscanned_tail_code_units:text.length-frontier.u,unknown_tail_candidate_count:unknown?null:0,gap:record};
            charge(bytes(observation));return observation;
        }finally{queue=null;unknown=null;}
    };
    try{
        if(rowLocator)return locate();
        if(rowFragment){
            if(peek().kind==='eof')gap('empty_fragment');
            arrowRows(true);if(peek().kind!=='eof')gap('unsupported_suffix');
            const facts={fragment_span:span({u:0,b:0,line:1,column:0},position()),entries:rows};
            metrics.fact_bytes=bytes(facts);metrics.name_utf8_bytes=nameBytes;charge(metrics.fact_bytes);
            need(metrics.fact_bytes<=limits.fact_bytes,'recipe_budget');
            const sorted=[...names].sort((a,b)=>Buffer.compare(Buffer.from(a),Buffer.from(b)));
            return {facts,observation:{fragment_syntax_complete:true,name_count:rows.length,fragment_units:text.length,
                fragment_facts_sha256:hash(facts),name_set_sha256:hash(sorted),gap:null}};
        }
        if(helperSite){
            const helper=expect('__export','word');expect('(');const target=identifier();expect(',');
            const tableStart=expect('{').start;if(punctToken(peek(),'}'))gap('empty_table');
            arrowRows(false);
            expect('}');const tableEnd=last.end;expect(')');expect(';');const siteEnd=last.end;
            if(peek().kind!=='eof')gap('unsupported_suffix');
            const facts={site_span:span(helper.start,siteEnd),helper:{spelling:helper.value,identity_resolved:false,span:tokenSpan(helper)},
                target:{reference_identifier:target.value,binding_resolved:false,span:tokenSpan(target)},table:{span:span(tableStart,tableEnd),entries:rows}};
            if(windowInfo)facts.window={entry_context_sha256:windowInfo.context,selection_sha256:windowInfo.selection};
            metrics.fact_bytes=bytes(facts);metrics.name_utf8_bytes=nameBytes;charge(metrics.fact_bytes);
            need(metrics.fact_bytes<=limits.fact_bytes,'recipe_budget');
            const sorted=[...names].sort((a,b)=>Buffer.compare(Buffer.from(a),Buffer.from(b)));
            if(windowInfo)return {facts,observation:{view_syntax_complete:true,name_count:rows.length,local_site_units:siteEnd.u-helper.start.u,
                view_facts_sha256:hash(facts),table_sha256:hash(facts.table),name_set_sha256:hash(sorted),gap:null}};
            return {facts,observation:{owned_fixture_frame_matched:true,complete_site_syntax_within_grammar:true,name_count:rows.length,
                site_units:siteEnd.u-helper.start.u,site_facts_sha256:hash(facts),table_sha256:hash(facts.table),name_set_sha256:hash(sorted),gap:null}};
        }
        const start=expect('const','word').start,local=identifier();if(['Object','module','exports'].includes(local.value))gap('target_binding',local.start,local.end);
        expect('=');callHead('create');expect('null','word');expect(')');expect(';');
        const construction={callee:'Object.create',argument:'null',span:span(start,last.end)},binding={name:local.value,span:tokenSpan(local)};
        const tableStart=peek().start;callHead('defineProperties');target(local.value);expect(',');expect('{');
        if(punctToken(peek(),'}'))gap('empty_table');
        while(true){
            const key=name();if(names.has(key.value))gap('duplicate_name',key.start,key.end);names.add(key.value);
            nameBytes=sum(nameBytes,Buffer.byteLength(key.value,'utf8'));need(nameBytes<=limits.name_utf8_bytes,'recipe_budget');
            expect(':');const descriptor=slots();
            rows.push({name:key.value,key_kind:key.kind,key_span:tokenSpan(key),entry_span:span(key.start,last.end),...descriptor});
            need(rows.length<=limits.row_count&&bytes(rows)<=limits.fact_bytes,'recipe_budget');
            if(!punctToken(peek(),','))break;expect(',');if(punctToken(peek(),'}'))break;
        }
        expect('}');expect(')');expect(';');const tableSpan=span(tableStart,last.end);
        const integrityStart=peek().start;expect('Object','word');expect('.');const method=peek();
        if(method.kind!=='word'||!['freeze','seal','preventExtensions'].includes(method.value))gap('unsupported_integrity');take();
        expect('(');target(local.value);expect(')');expect(';');const integrity={callee:'Object.'+method.value,span:span(integrityStart,last.end)};
        const bridgeStart=peek().start;callHead('defineProperty');expect('module','word');expect(',');expect('exports','string');expect(',');
        const bridgeSlots=slots();expect(')');expect(';');
        if(bridgeSlots.slots.length!==1||bridgeSlots.slots[0].slot!=='get'||bridgeSlots.slots[0].reference_identifier!==local.value)gap('unsupported_bridge',last.start,last.end);
        const bridge={receiver:'module',property:'exports',target:local.value,span:span(bridgeStart,last.end),...bridgeSlots};
        if(peek().kind!=='eof')gap('unsupported_suffix');
        const facts={target:binding,construction,table:{span:tableSpan,entries:rows},integrity,bridge};
        metrics.fact_bytes=bytes(facts);metrics.name_utf8_bytes=nameBytes;charge(metrics.fact_bytes);
        need(metrics.fact_bytes<=limits.fact_bytes,'recipe_budget');
        const sorted=[...names].sort((a,b)=>Buffer.compare(Buffer.from(a),Buffer.from(b)));
        return {facts,observation:{complete_recipe_within_grammar:true,name_count:rows.length,recipe_sha256:hash(facts),name_set_sha256:hash(sorted),gap:null}};
    }catch(error){
        if(!(error instanceof SourceGap))throw error;
        rows=null;names.clear();metrics.fact_bytes=metrics.name_utf8_bytes=0;
        const record={code:error.code,...span(error.start,error.end)};metrics.gap_bytes=bytes(record);need(metrics.gap_bytes<=limits.gap_bytes,'recipe_budget');
        if(rowFragment)return {facts:null,observation:{fragment_syntax_complete:false,name_count:0,fragment_units:0,fragment_facts_sha256:null,name_set_sha256:null,gap:record}};
        return {facts:null,observation:windowInfo?{view_syntax_complete:false,name_count:0,local_site_units:0,view_facts_sha256:null,table_sha256:null,name_set_sha256:null,gap:record}:helperSite?{owned_fixture_frame_matched:false,complete_site_syntax_within_grammar:false,name_count:0,
            site_units:0,site_facts_sha256:null,table_sha256:null,name_set_sha256:null,gap:record}:
            {complete_recipe_within_grammar:false,name_count:0,recipe_sha256:null,name_set_sha256:null,gap:record}};
    }finally{look=last=rows=text=windowInfo=null;names.clear();}
}

function recognize(text,limits,check,metrics,helperSite,windowInfo=null){return recognizeInternal(text,limits,check,metrics,helperSite,windowInfo);}
function recognizeRowFragment(text,limits,check,metrics){return recognizeInternal(text,limits,check,metrics,true,null,true);}
function locateRowCandidates(text,limits,check,metrics,grammarIdentity){return recognizeInternal(text,limits,check,metrics,true,null,false,true,grammarIdentity);}
module.exports={recognize,recognizeRowFragment,locateRowCandidates,CompilerExportRecipeRefusal,need,sum};
